import discord
import asyncio
import aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import json
from datetime import datetime, time, timedelta
import anthropic
import os
from dotenv import load_dotenv

# Load environment variables from .env if it exists (for local development)
load_dotenv()

# ==================== CONFIG ====================
CONFIG_FILE = "config.json"
FIRST_RUN_TIME = datetime.now() - timedelta(hours=24)  # Czyta wiadomości z ostatnich 24h
DAILY_MESSAGES = []  # Przechowuje wiadomości dla codziennego raportu

def load_config():
    """Load config from JSON file and override with environment variables"""
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except FileNotFoundError:
        print(f"ERROR: {CONFIG_FILE} not found!")
        return None
    
    # Override with environment variables (for Railway.app)
    config['discord_token'] = os.getenv('DISCORD_TOKEN', config.get('discord_token', ''))
    config['gmail_address'] = os.getenv('GMAIL_ADDRESS', config.get('gmail_address', ''))
    config['gmail_password'] = os.getenv('GMAIL_PASSWORD', config.get('gmail_password', ''))
    config['anthropic_api_key'] = os.getenv('ANTHROPIC_API_KEY', '')
    
    return config

config = load_config()

if not config:
    print("ERROR: Could not load config!")
    exit(1)

DISCORD_TOKEN = config.get("discord_token")
GMAIL_ADDRESS = config.get("gmail_address")
GMAIL_PASSWORD = config.get("gmail_password")
RECIPIENT_EMAIL = config.get("recipient_email", GMAIL_ADDRESS)
REPORT_HOUR = config.get("report_hour", 8)
REPORT_MINUTE = config.get("report_minute", 0)
STOCKS_AND_ETFS = config.get("stocks_and_etfs", [])
ANTHROPIC_API_KEY = config.get("anthropic_api_key") or os.getenv("ANTHROPIC_API_KEY", "")

# Validate required config
if not DISCORD_TOKEN:
    print("ERROR: DISCORD_TOKEN not set!")
    exit(1)
if not ANTHROPIC_API_KEY:
    print("ERROR: ANTHROPIC_API_KEY not set!")
    exit(1)
if not GMAIL_ADDRESS or not GMAIL_PASSWORD:
    print("ERROR: Gmail credentials not set!")
    exit(1)

# ==================== DISCORD CLIENT ====================

class DiscordMonitor(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.all())
        self.collected_messages = []
        self.ready = False

    async def on_ready(self):
        print(f"✅ Bot zalogowany jako: {self.user}")
        print(f"⏰ Czas uruchomienia: {FIRST_RUN_TIME.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"📊 Codzienne podsumowanie o: {REPORT_HOUR:02d}:{REPORT_MINUTE:02d}")
        self.ready = True

    async def on_message(self, message):
        # Ignoruj własne wiadomości
        if message.author == self.user:
            return
        
        # Ignoruj wiadomości starsze niż uruchomienie bota
        if message.created_at < FIRST_RUN_TIME:
            return
        
        author_name = message.author.name
        channel_name = message.channel.name if hasattr(message.channel, 'name') else str(message.channel)
        content = message.content
        
        # Store all messages from channels we can read
        self.collected_messages.append({
            "author": author_name,
            "author_id": message.author.id,
            "content": content,
            "channel": channel_name,
            "timestamp": message.created_at.isoformat()
        })
        
        # Check for instant alerts - TYLKO dla ważnych autorów i kanałów
        should_alert = False
        alert_type = None
        alert_asset = None
        
        # Alert 1: Wszystkie wpisy z kanałów portfel-*
        if channel_name.startswith("portfel-"):
            should_alert = True
            alert_type = "AKCJA PORTFELA"
            alert_asset = channel_name.replace("portfel-", "").replace("-", " ").title()
        
        # Alert 2: Wpisy Piotra (dnarynkow) lub Jurka (jurek_dna) z kanałów przemyślenia
        elif author_name in ["dnarynkow", "jurek_dna"] and channel_name in ["przemyślenia-piotr", "przemyślenia-jurek"]:
            # Tylko jeśli ma słowa klucze akcji
            action_keywords = ["kupuję", "kupię", "sprzedaję", "sprzedaż", "zwiększam", "zmniejszam", "wychodzę", "wchodzę", "kupna", "sprzedaży", "buy", "sell"]
            if any(keyword in content.lower() for keyword in action_keywords):
                should_alert = True
                alert_type = "EKSPERCKA OPINIA"
                alert_asset = "Ogólna analiza"
        
        # Alert 3: Wpisy Piotra/Jurka w kanałach spółek
        elif author_name in ["dnarynkow", "jurek_dna"]:
            should_alert = True
            alert_type = "WPIS EKSPERTA"
            alert_asset = channel_name
        
        # Wyślij alert jeśli spełnia warunki i dodaj do dziennego podsumowania
        if should_alert:
            alert_data = {
                "author": author_name,
                "channel": channel_name,
                "message": content[:500],  # Ogranicz do 500 znaków
                "type": alert_type,
                "asset": alert_asset,
                "relevance": "wysoka",
                "timestamp": message.created_at.isoformat()
            }
            
            # Wyślij natychmiastowy alert
            await send_instant_alert(alert_data)
            
            # Dodaj do dziennego podsumowania
            DAILY_MESSAGES.append(alert_data)

# ==================== EMAIL ====================

async def send_email(subject, body_html):
    """Send email via Gmail SMTP"""
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = GMAIL_ADDRESS
        msg['To'] = RECIPIENT_EMAIL
        
        msg.attach(MIMEText(body_html, 'html'))
        
        async with aiosmtplib.SMTP(hostname='smtp.gmail.com', port=587) as smtp:
            await smtp.starttls()
            await smtp.login(GMAIL_ADDRESS, GMAIL_PASSWORD)
            await smtp.send_message(msg)
        
        print(f"✅ Email wysłany: {subject}")
        return True
    except Exception as e:
        print(f"❌ Błąd wysyłania emaila: {e}")
        return False

async def send_instant_alert(message_data):
    """Send instant alert for important messages"""
    author = message_data.get("author", "Anonimowy")
    channel = message_data.get("channel", "unknown")
    content = message_data.get("message", "Brak treści")
    msg_type = message_data.get("type", "WIADOMOŚĆ")
    asset = message_data.get("asset", "")
    
    # Ustaw temat i styl alertu
    if msg_type == "AKCJA PORTFELA":
        icon = "🚀"
        color = "#D32F2F"
        subject = f"🚀 ALERT PORTFELA: {asset}"
    elif msg_type == "EKSPERCKA OPINIA" or msg_type == "WPIS EKSPERTA":
        icon = "⭐"
        color = "#FF6B35"
        subject = f"⭐ ALERT EKSPERTA: {asset or 'Ogólna analiza'}"
    else:
        icon = "📌"
        color = "#0066cc"
        subject = f"📌 ALERT: {asset or channel}"
    
    html = f"""
    <html>
        <head><meta charset="UTF-8"></head>
        <body style="font-family: Arial, sans-serif; color: #333;">
            <div style="background: linear-gradient(135deg, {color}, {color}dd); padding: 20px; border-radius: 8px; color: white; margin-bottom: 20px;">
                <h2 style="margin: 0; font-size: 24px;">{icon} WAŻNA WIADOMOŚĆ!</h2>
                <p style="margin: 5px 0 0 0; font-size: 14px;">Przyszła natychmiast - przeczytaj!</p>
            </div>
            
            <div style="background: #f9f9f9; padding: 15px; border-left: 4px solid {color}; border-radius: 4px;">
                <p style="margin: 0 0 10px 0;">
                    <strong style="color: {color};">@{author}</strong>
                    <span style="color: #999; font-size: 12px;">#{channel}</span>
                </p>
                <p style="margin: 0 0 10px 0; font-size: 14px; color: #333;">
                    {content}
                </p>
                <p style="margin: 0; font-size: 12px; color: #666;">
                    Typ: <span style="color: {color}; font-weight: bold;">{msg_type}</span><br>
                    Czas: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
                </p>
            </div>
            
            <hr style="margin-top: 20px; border: none; border-top: 1px solid #ddd;">
            <p style="font-size: 12px; color: #999; margin-top: 15px;">
                Raport wysłany automatycznie przez Discord Monitor Bot
            </p>
        </body>
    </html>
    """
    
    await send_email(subject, html)

# ==================== CLAUDE AI ANALYSIS ====================

def analyze_messages_with_claude(messages_text):
    """Use Claude to analyze messages and find relevant ones"""
    
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    
    stocks_list = ", ".join(STOCKS_AND_ETFS)
    
    prompt = f"""Jesteś analizatorem wiadomości z kanału inwestycyjnego Discord dla polska giełdy.

TWOJA ROLA: Przeanalizuj poniższe wiadomości i wyodrębnij TYLKO te, które są istotne dla inwestora.

EKSPERCI: dnarynkow (Piotr) i jurek_dna (Jurek)

PRIORYTET 1 (SUPER WAŻNE - ZAWSZE BIERZ): 
- WSZYSTKIE wiadomości z kanałów "portfel-*" (portfel-kwartalny-etf, portfel-agresywny-freedom, portfel-emerytany-xtb, portfel-defensywny-saxo, portfel-szaleniec, portfel-kilimandżaro, portfel-gubalowka, portfel-krypto)
- Te kanały zawierają AKCJE kupna/sprzedaży/zwiększenia/zmniejszenia pozycji Piotra
- Oznacz wszystkie jako "AKCJA PORTFELA" i relevance = "wysoka"

PRIORYTET 2 (ZAWSZE BIERZ JEŚLI MA AKCJĘ): 
- Wiadomości z kanałów "przemyślenia-jurek" i "przemyślenia-piotr"
- ALE TYLKO jeśli sugerują: kupno, sprzedaż, zwiększenie pozycji, zmniejszenie pozycji, czy zmianę
- Ignoruj: ogólne dyskusje, analizy bez konkretnego działania

PRIORYTET 3 (ZAWSZE BIERZ JEŚLI PIOTR/JUREK):
- Wpisy z kanałów konkretnych spółek
- ALE TYLKO jeśli autor to dnarynkow (Piotr) lub jurek_dna (Jurek)
- I jeśli wpis jest istotny (nie ogólna dyskusja)

PRIORYTET 4 (Niskiprioritet):
- Wpisy o monitowanych aktywach od innych osób (nie Piotra/Jurka)
- Tylko jeśli dotyczą kupna/sprzedaży/akcji

PRIORYTET 5:
- Propozycje KUPNA/SPRZEDAŻY nowych spółek od Piotra lub Jurka

MONITOROWANE AKTYWA:
{stocks_list}

WIADOMOŚCI Z DISCORD:
{messages_text}

INSTRUKCJE:
1. Wszystkie wpisy z kanałów "portfel-*" → ZAWSZE BIERZ (to są AKCJE)
2. Wpisy z "przemyślenia-jurek" i "przemyślenia-piotr" → BIERZ TYLKO jeśli sugerują AKCJĘ (kupno/sprzedaż/zmianę pozycji)
3. Wpisy ze spółek → BIERZ TYLKO jeśli autor to dnarynkow lub jurek_dna i coś istotnego
4. Propozycje nowych spółek → BIERZ TYLKO od Piotra/Jurka
5. Claude musi rozumieć KONTEKST - np Elon Musk = Tesla, półprzewodniki = chip stocks, itp
6. Dla każdej wiadomości podaj:
   - Które aktywo dotyczy
   - Treść (streszczenie)
   - Autora
   - Kanał
   - Typ: "AKCJA PORTFELA" | "EKSPERCKA OPINIA" | "WPIS EKSPERTA" | "PROPOZYCJA NOWA"

ODPOWIEDŹ - zwróć czystą listę w formacie JSON bez dodatkowego tekstu:
[
  {{
    "asset": "nazwa aktywu lub 'Akcja portfela' lub 'Opinia eksperta'",
    "author": "nazwa autora",
    "channel": "nazwa kanału",
    "message": "streszczenie wiadomości (1-3 zdania)",
    "relevance": "wysoka" | "średnia",
    "type": "AKCJA PORTFELA" | "EKSPERCKA OPINIA" | "WPIS EKSPERTA" | "PROPOZYCJA NOWA"
  }},
  ...
]

Jeśli brak istotnych wiadomości, zwróć: []
"""
    
    message = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=2000,
        messages=[
            {"role": "user", "content": prompt}
        ]
    )
    
    response_text = message.content[0].text.strip()
    
    # Parse JSON response
    try:
        # Handle case where response might have markdown code blocks
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0].strip()
        
        relevant_messages = json.loads(response_text)
        return relevant_messages
    except json.JSONDecodeError:
        print(f"❌ Błąd parsowania JSON z Claude: {response_text}")
        return []

# ==================== REPORT GENERATION ====================

def generate_html_report(relevant_messages):
    """Generate HTML email report"""
    
    now = datetime.now()
    
    if not relevant_messages:
        html = f"""
        <html>
            <head><meta charset="UTF-8"></head>
            <body style="font-family: Arial, sans-serif; color: #333;">
                <h2>📊 Raport Discord - DNA Rynków</h2>
                <p><strong>Data:</strong> {now.strftime("%Y-%m-%d %H:%M")}</p>
                <hr>
                <p style="color: #666; font-size: 14px;">
                    Dzisiaj brak istotnych wiadomości dotyczących Twoich aktywów na kanałach premium.
                </p>
                <hr>
                <p style="font-size: 12px; color: #999;">
                    Raport wygenerowany automatycznie przez Discord Monitor + Claude AI na Railway.app
                </p>
            </body>
        </html>
        """
        return html
    
    # Sort by type: AKCJA PORTFELA first, then others
    portfolio_actions = [m for m in relevant_messages if m.get("type") == "AKCJA PORTFELA"]
    expert_posts = [m for m in relevant_messages if m.get("type") == "WPIS EKSPERTA"]
    expert_opinions = [m for m in relevant_messages if m.get("type") == "EKSPERCKA OPINIA"]
    monitored_messages = [m for m in relevant_messages if m.get("type") == "MONITOROWANE AKTYWO"]
    new_proposal_messages = [m for m in relevant_messages if m.get("type") == "PROPOZYCJA NOWA"]
    
    # Build HTML
    html = f"""
    <html>
        <head><meta charset="UTF-8"></head>
        <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
            <h2>📊 Raport Discord - DNA Rynków</h2>
            <p><strong>Data:</strong> {now.strftime("%Y-%m-%d %H:%M")}</p>
            <p><strong>Znaleziono:</strong> {len(relevant_messages)} istotnych wiadomości</p>
            <p style="color: #666; font-size: 13px; font-style: italic;">
                Uwaga: Niektóre z tych wiadomości mogły przysłać się jako natychmiastowe alerty.
            </p>
            <hr>
    """
    
    # Portfolio actions section - TOP PRIORITY
    if portfolio_actions:
        html += f"""
            <h3 style="color: #D32F2F; margin-top: 20px;">🚀 AKCJE PORTFELA (Piotr)</h3>
            <p style="color: #C62828; font-size: 13px; margin-top: 0; font-weight: bold;">⚡ KUPNA/SPRZEDAŻA - możesz je kopiować do swojego portfela!</p>
        """
        for msg in portfolio_actions:
            html += f"""
            <div style="background: #FFEBEE; padding: 14px; margin: 10px 0; border-left: 4px solid #D32F2F; border-radius: 4px; border: 1px solid #EF5350;">
                <p style="margin: 0 0 8px 0;">
                    <strong style="color: #D32F2F; font-size: 16px;">🚀 {msg.get('asset', 'Akcja')}</strong>
                    <span style="color: #999; font-size: 12px;">#{msg.get('channel', 'unknown')}</span>
                </p>
                <p style="margin: 0; color: #333; font-weight: 500; font-size: 14px;">
                    {msg.get('message', 'Brak treści')}
                </p>
                <p style="margin: 5px 0 0 0; font-size: 11px; color: #D32F2F;">
                    Istotność: <span style="font-weight: bold;">{msg.get('relevance', 'nieznana').upper()}</span>
                </p>
            </div>
            """
    
    # Expert opinions section
    if expert_opinions:
        html += f"""
            <h3 style="color: #FF6B35; margin-top: 20px;">⭐ EKSPERCKIE OPINIE (Przemyślenia)</h3>
            <p style="color: #666; font-size: 13px; margin-top: 0;">Wpisy Piotra/Jurka sugerujące akcje (kupno/sprzedaż/zmianę pozycji)</p>
        """
        for msg in expert_opinions:
            html += f"""
            <div style="background: #fff3e0; padding: 12px; margin: 10px 0; border-left: 4px solid #FF6B35; border-radius: 4px;">
                <p style="margin: 0 0 8px 0;">
                    <strong style="color: #FF6B35;">⭐ @{msg.get('author', 'Anonimowy')}</strong>
                    <span style="color: #999; font-size: 12px;">#{msg.get('channel', 'unknown')}</span>
                </p>
                <p style="margin: 0; color: #333; font-weight: 500;">
                    {msg.get('message', 'Brak treści')}
                </p>
                <p style="margin: 5px 0 0 0; font-size: 11px; color: #FF6B35;">
                    Istotność: <span style="font-weight: bold;">{msg.get('relevance', 'nieznana').upper()}</span>
                </p>
            </div>
            """
    
    # Expert posts in asset channels section
    if expert_posts:
        html += f"""
            <h3 style="color: #9C27B0; margin-top: 20px;">💬 WPISY EKSPERTÓW W KANAŁACH SPÓŁEK</h3>
            <p style="color: #666; font-size: 13px; margin-top: 0;">Wpisy Piotra/Jurka dotyczące konkretnych spółek</p>
        """
        for msg in expert_posts:
            html += f"""
            <div style="background: #F3E5F5; padding: 12px; margin: 10px 0; border-left: 4px solid #9C27B0; border-radius: 4px;">
                <p style="margin: 0 0 8px 0;">
                    <strong style="color: #9C27B0;">💬 {msg.get('asset', 'Spółka')}</strong>
                    <span style="color: #999; font-size: 12px;">od @{msg.get('author', 'Anonimowy')}</span>
                </p>
                <p style="margin: 0; color: #333;">
                    {msg.get('message', 'Brak treści')}
                </p>
                <p style="margin: 5px 0 0 0; font-size: 11px; color: #9C27B0;">
                    Istotność: <span style="font-weight: bold;">{msg.get('relevance', 'nieznana').upper()}</span>
                </p>
            </div>
            """
    
    # Monitored assets section
    if monitored_messages:
        html += f"""
            <h3 style="color: #0066cc; margin-top: 20px;">📌 MONITOROWANE AKTYWA</h3>
        """
        # Group by asset
        assets_grouped = {}
        for msg in monitored_messages:
            asset = msg.get("asset", "Nieznane")
            if asset not in assets_grouped:
                assets_grouped[asset] = []
            assets_grouped[asset].append(msg)
        
        for asset, messages in assets_grouped.items():
            html += f"""
            <h4 style="color: #0066cc; margin: 15px 0 8px 0;">{asset}</h4>
            """
            for msg in messages:
                relevance_color = "#FF6B6B" if msg.get("relevance") == "wysoka" else "#FFA500"
                html += f"""
            <div style="background: #f5f5f5; padding: 12px; margin: 10px 0; border-left: 4px solid {relevance_color}; border-radius: 4px;">
                <p style="margin: 0 0 8px 0;">
                    <strong>@{msg.get('author', 'Anonimowy')}</strong>
                    <span style="color: #999; font-size: 12px;">#{msg.get('channel', 'unknown')}</span>
                </p>
                <p style="margin: 0; color: #555;">
                    {msg.get('message', 'Brak treści')}
                </p>
                <p style="margin: 5px 0 0 0; font-size: 11px; color: #999;">
                    Istotność: <span style="color: {relevance_color}; font-weight: bold;">{msg.get('relevance', 'nieznana').upper()}</span>
                </p>
            </div>
                """
    
    # New proposals section
    if new_proposal_messages:
        html += f"""
            <h3 style="color: #2E7D32; margin-top: 20px;">💡 PROPOZYCJE NOWYCH SPÓŁEK</h3>
            <p style="color: #666; font-size: 13px; margin-top: 0;">Interesujące spółki zaproponowane przez ekspertów (nie na Twojej liście)</p>
        """
        for msg in new_proposal_messages:
            html += f"""
            <div style="background: #e8f5e9; padding: 12px; margin: 10px 0; border-left: 4px solid #2E7D32; border-radius: 4px;">
                <p style="margin: 0 0 8px 0;">
                    <strong style="color: #2E7D32;">💡 {msg.get('asset', 'Nowa spółka')}</strong>
                    <span style="color: #999; font-size: 12px;">od @{msg.get('author', 'Anonimowy')}</span>
                </p>
                <p style="margin: 0; color: #333;">
                    {msg.get('message', 'Brak treści')}
                </p>
                <p style="margin: 5px 0 0 0; font-size: 11px; color: #2E7D32;">
                    Istotność: <span style="font-weight: bold;">{msg.get('relevance', 'nieznana').upper()}</span>
                </p>
            </div>
            """
    
    html += """
        <hr style="margin-top: 30px;">
        <p style="font-size: 12px; color: #999;">
            Raport wygenerowany automatycznie przez Discord Monitor + Claude AI na Railway.app
        </p>
    </body>
    </html>
    """
    
    return html

# ==================== MAIN LOOP ====================

async def main():
    bot = DiscordMonitor()
    
    # Start bot in background
    bot_task = asyncio.create_task(bot.start(DISCORD_TOKEN))
    
    try:
        # Wait for bot to be ready
        while not bot.ready:
            await asyncio.sleep(1)
        
        print("🚀 Bot jest gotowy. Czekam na wiadomości...")
        
        # Main loop - check time every minute
        while True:
            now = datetime.now()
            current_time = time(now.hour, now.minute)
            target_time = time(REPORT_HOUR, REPORT_MINUTE)
            
            # Check if it's time to generate report
            if current_time == target_time:
                print(f"\n⏰ {now.strftime('%H:%M')} - GENEROWANIE RAPORTU")
                
                if bot.collected_messages or DAILY_MESSAGES:
                    # Combine daily alerts + newly collected messages
                    all_messages = DAILY_MESSAGES.copy()
                    
                    # Add newly collected messages to analyze
                    if bot.collected_messages:
                        # Format messages for Claude
                        messages_text = "\n".join([
                            f"[{m['timestamp']}] #{m['channel']} @{m['author']}: {m['content']}"
                            for m in bot.collected_messages
                        ])
                        
                        # Analyze with Claude
                        analyzed = analyze_messages_with_claude(messages_text)
                        if analyzed:
                            all_messages.extend(analyzed)
                    
                    # Generate and send report
                    if all_messages:
                        html_report = generate_html_report(all_messages)
                        await send_email(
                            f"📊 Raport Discord DNA Rynków - {now.strftime('%Y-%m-%d')}",
                            html_report
                        )
                        print(f"✅ Raport wysłany! Znaleziono: {len(all_messages)} wiadomości")
                    
                    # Reset daily messages for next day
                    DAILY_MESSAGES.clear()
                    bot.collected_messages.clear()
                else:
                    print("ℹ️ Brak wiadomości do raportu")
            
            await asyncio.sleep(60)  # Check every minute
    
    except Exception as e:
        print(f"❌ Błąd w main loop: {e}")
    finally:
        await bot.close()

# ==================== RUN ====================

if __name__ == "__main__":
    asyncio.run(main())
