import discord
import asyncio
import aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import json
from datetime import datetime, time
import anthropic
import os
from dotenv import load_dotenv

# Load environment variables from .env if it exists (for local development)
load_dotenv()

# ==================== CONFIG ====================
CONFIG_FILE = "config.json"

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
if not GMAIL_ADDRESS or not GMAIL_PASSWORD:
    print("ERROR: Gmail credentials not set!")
    exit(1)
if not ANTHROPIC_API_KEY:
    print("ERROR: ANTHROPIC_API_KEY not set!")
    exit(1)

print(f"✅ Config loaded successfully")
print(f"   Discord Token: {DISCORD_TOKEN[:20]}...")
print(f"   Gmail: {GMAIL_ADDRESS}")
print(f"   Recipient: {RECIPIENT_EMAIL}")
print(f"   Report time: {REPORT_HOUR:02d}:{REPORT_MINUTE:02d}")
print(f"   Monitoring {len(STOCKS_AND_ETFS)} assets")

# ==================== DISCORD BOT ====================

class DiscordMonitor(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.all())
        self.collected_messages = []
        self.ready = False

    async def on_ready(self):
        print(f"✅ Bot zalogowany jako: {self.user}")
        self.ready = True

    async def on_message(self, message):
        if message.author == self.user:
            return
        
        # Store all messages from channels we can read
        self.collected_messages.append({
            "author": message.author.name,
            "author_id": message.author.id,
            "content": message.content,
            "channel": message.channel.name if hasattr(message.channel, 'name') else str(message.channel),
            "timestamp": message.created_at.isoformat()
        })

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

# ==================== CLAUDE AI ANALYSIS ====================

def analyze_messages_with_claude(messages_text):
    """Use Claude to analyze messages and find relevant ones"""
    
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    
    stocks_list = ", ".join(STOCKS_AND_ETFS)
    
    prompt = f"""Jesteś analizatorem wiadomości z kanału inwestycyjnego Discord dla polska giełdy.

TWOJA ROLA: Przeanalizuj poniższe wiadomości i wyodrębnij TYLKO te, które są istotne dla inwestora.

PRIORYTET 1 (SUPER WAŻNE - ZAWSZE BIERZ): 
- WSZYSTKIE wiadomości z kanałów "portfel-*" (portfel-kwartalny-etf, portfel-agresywny-freedom, portfel-emerytany-xtb, portfel-defensywny-saxo, portfel-szaleniec, portfel-kilimandżaro, portfel-gubalowka, portfel-krypto)
- Te kanały zawierają AKCJE kupna/sprzedaży Piotra - możesz je kopiować!
- Oznacz wszystkie jako "AKCJA KUPNA/SPRZEDAŻY" i relevance = "wysoka"

PRIORYTET 2 (ZAWSZE BIERZ): 
- WSZYSTKIE wiadomości z kanałów "przemyślenia-jurek" i "przemyślenia-piotr"
- Te kanały zawierają cenne spostrzeżenia tych ekspertów

PRIORYTET 3:
- Wiadomości dotyczące DOWOLNEGO z tych aktywów:
{stocks_list}
- Rozumiej kontekst - np "S&P 500" = iShares Core S&P 500, "Brazil" = MSCI Brazil ETF

PRIORYTET 4:
- Propozycje KUPNA/SPRZEDAŻY/ANALIZY spółek którymi inwestor się nie zajmuje (ale które mogą być interesujące)
- "Wspomnę o akcjach MEVO" = bierz to nawet jeśli MEVO nie jest na liście

WIADOMOŚCI Z DISCORD:
{messages_text}

INSTRUKCJE:
1. Wszystkie wpisy z kanałów "portfel-*" → ZAWSZE BIERZ (wysoka relevance) - to są AKCJE!
2. Wszystkie wpisy z "przemyślenia-jurek" i "przemyślenia-piotr" → ZAWSZE BIERZ (wysoka relevance)
3. Wpisy o monitowanych aktywach → bierz (wysoka/średnia relevance)
4. Propozycje kupna/sprzedaży/analizy nowych spółek → bierz (średnia relevance)
5. Ignoruj: ogólne dyskusje, memy, powitania, szum bez wartości
6. Dla każdej wiadomości podaj:
   - Które aktywo dotyczy (lub "Akcja portfela" dla portfel-*, "Ogólna analiza" dla przemyśleń)
   - Treść (streszczenie)
   - Autora
   - Kanał
   - Typ: "AKCJA PORTFELA" jeśli z kanału portfel-*, "EKSPERCKA OPINIA" jeśli z przemyśleń, "MONITOROWANE AKTYWO" jeśli o Twoim, "PROPOZYCJA NOWA" jeśli nowa spółka

ODPOWIEDŹ - zwróć czystą listę w formacie JSON bez dodatkowego tekstu:
[
  {{
    "asset": "nazwa aktywu lub 'Akcja portfela' lub 'Ogólna analiza'",
    "author": "nazwa autora",
    "channel": "nazwa kanału",
    "message": "streszczenie wiadomości (1-3 zdania)",
    "relevance": "wysoka" | "średnia",
    "type": "AKCJA PORTFELA" | "EKSPERCKA OPINIA" | "MONITOROWANE AKTYWO" | "PROPOZYCJA NOWA"
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
    
    # Sort by type: AKCJA PORTFELA first, then EKSPERCKA OPINIA, then others
    portfolio_actions = [m for m in relevant_messages if m.get("type") == "AKCJA PORTFELA"]
    expert_messages = [m for m in relevant_messages if m.get("type") == "EKSPERCKA OPINIA"]
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
    if expert_messages:
        html += f"""
            <h3 style="color: #FF6B35; margin-top: 20px;">⭐ EKSPERCKIE OPINIE (Piotr & Jurek)</h3>
            <p style="color: #666; font-size: 13px; margin-top: 0;">Te wpisy zawierają cenne spostrzeżenia od ekspertów</p>
        """
        for msg in expert_messages:
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
                
                if bot.collected_messages:
                    # Format messages for Claude
                    messages_text = "\n---\n".join([
                        f"[{msg['timestamp']}] @{msg['author']} (#{msg['channel']}): {msg['content']}"
                        for msg in bot.collected_messages[-500:]  # Last 500 messages
                    ])
                    
                    print(f"📊 Analizuję {len(bot.collected_messages)} zebranych wiadomości...")
                    
                    # Analyze with Claude
                    relevant_messages = analyze_messages_with_claude(messages_text)
                    print(f"✅ Znaleziono {len(relevant_messages)} istotnych wiadomości")
                    
                    # Generate and send report
                    html_report = generate_html_report(relevant_messages)
                    await send_email(
                        subject=f"📊 Raport Discord DNA Rynków - {now.strftime('%Y-%m-%d')}",
                        body_html=html_report
                    )
                    
                    # Clear collected messages
                    bot.collected_messages = []
                else:
                    print("⚠️ Brak zebranych wiadomości")
                    html_report = generate_html_report([])
                    await send_email(
                        subject=f"📊 Raport Discord DNA Rynków - {now.strftime('%Y-%m-%d')}",
                        body_html=html_report
                    )
                
                # Wait 61 seconds to avoid duplicate reports
                await asyncio.sleep(61)
            
            # Check every 30 seconds
            await asyncio.sleep(30)
    
    except KeyboardInterrupt:
        print("\n⚠️ Bot wyłączony")
    finally:
        await bot.close()

if __name__ == "__main__":
    asyncio.run(main())
