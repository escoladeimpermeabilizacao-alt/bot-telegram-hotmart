import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import uvicorn

# --- 1. CONFIGURAÇÕES ---
TOKEN_TELEGRAM = os.getenv("TOKEN_TELEGRAM", "SEU_TOKEN_FIXO_AQUI") "8415807755:AAEKweJtrrA2-s8UKqeBUpLJojgRiMeS9Lk"
GRUPO_ID = int(os.getenv("GRUPO_ID", "-1003394118030"))


# Simulação de Banco de Dados
# Estrutura nova: { "email": {"status": "...", "telegram_id": 123, "invite_link": "https://..."} }
db_alunos = {}

# --- 2. LÓGICA DO TELEGRAM (HANDLERS) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Olá! Sou o guardião do grupo de membros do Telegram da Escola de Impermeabilização.\n"
        "Para liberar seu acesso, por favor, digite o **email** que você usou na compra da Hotmart.\n\n"
        "Caso você tenha problemas comigo, envie uma mensagem com seu comprovante de compra em um de nossos canais de contato."
    )

async def receber_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email_usuario = update.message.text.lower().strip()
    novo_user_id = update.effective_user.id
    
    aluno = db_alunos.get(email_usuario)

    if aluno and aluno['status'] == 'approved':
        try:
            # --- SEGURANÇA TOTAL (ANTI-COMPARTILHAMENTO) ---
            # 1. Recupera dados antigos
            id_antigo = aluno.get('telegram_id')
            link_antigo = aluno.get('invite_link')
            
            # 2. Se já tinha alguém vinculado a este email (e não é a mesma pessoa de agora)
            if id_antigo and id_antigo != novo_user_id:
                try:
                    # Expulsa o usuário anterior do grupo (um derruba o outro)
                    await context.bot.ban_chat_member(chat_id=GRUPO_ID, user_id=id_antigo)
                    await context.bot.unban_chat_member(chat_id=GRUPO_ID, user_id=id_antigo)
                    print(f"♻️ TROCA DE DISPOSITIVO: Usuário antigo {id_antigo} removido para entrada de {novo_user_id}.")
                except Exception as e:
                    print(f"Aviso: Não foi possível remover usuário antigo (talvez já tenha saído): {e}")

            # 3. Revoga link antigo (se houver)
            if link_antigo:
                try:
                    await context.bot.revoke_chat_invite_link(chat_id=GRUPO_ID, invite_link=link_antigo)
                except:
                    pass

            # --- GERAÇÃO DO NOVO ACESSO ---
            convite = await context.bot.create_chat_invite_link(
                chat_id=GRUPO_ID, 
                member_limit=1, 
                name=f"Aluno {email_usuario}" 
            )
            
            # Atualiza o banco com o NOVO dono
            db_alunos[email_usuario]['telegram_id'] = novo_user_id
            db_alunos[email_usuario]['invite_link'] = convite.invite_link
            
            await update.message.reply_text(
                f"✅ Acesso Confirmado!\n\n"
                f"Aqui está seu link exclusivo e de **uso único**. Não compartilhe:\n{convite.invite_link}\n\n"
                f"⚠️ **Atenção:** Se você gerar um novo link, este anterior deixará de funcionar imediatamente."
                f"⚠️ **Importante:** Este login desconectou qualquer outro dispositivo que estivesse usando este e-mail no grupo."
            )
            print(f"LOGIN NOVO: {email_usuario} agora é ID {novo_user_id}")
            
        except Exception as e:
            await update.message.reply_text("Erro técnico ao gerar acesso.")
            print(f"ERRO: {e}")

    elif aluno and aluno['status'] != 'approved':
        await update.message.reply_text("Sua assinatura não está ativa.")
    else:
        await update.message.reply_text("❌ Email não encontrado.")

# --- 3. CRIAÇÃO DO BOT ---
ptb_app = Application.builder().token(TOKEN_TELEGRAM).build()
ptb_app.add_handler(CommandHandler("start", start))
ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receber_email))

# --- 4. GERENCIADOR DE CICLO DE VIDA ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 Iniciando Bot Telegram...")
    await ptb_app.initialize()
    await ptb_app.start()
    await ptb_app.updater.start_polling()
    print("🤖 Bot ONLINE e ESCUTANDO mensagens!")
    
    yield 
    
    print("🛑 Parando Bot...")
    await ptb_app.updater.stop()
    await ptb_app.stop()
    await ptb_app.shutdown()
    print("Bot Desligado.")

# --- 5. O SERVIDOR WEB ---
app = FastAPI(lifespan=lifespan)

# --- 6. O WEBHOOK DA HOTMART ---
@app.post("/webhook")
async def hotmart_webhook(request: Request):
    dados = await request.json()
    
    evento = dados.get("event")
    data = dados.get("data", {})
    buyer = data.get("buyer", {})
    email = buyer.get("email", "").lower()

    if not email:
        return {"status": "ignored", "reason": "no email"}

    print(f"📥 Hotmart Evento: {evento} -> {email}")

    if evento == "PURCHASE_APPROVED":
        # Inicia o registro apenas com status, sem link e sem ID ainda
        db_alunos[email] = {"status": "approved", "telegram_id": None, "invite_link": None}
        print(f"✅ LIBERADO: Aguardando {email} chamar no Telegram.")
    
    elif evento in ["SUBSCRIPTION_CANCELLATION", "REFUNDED", "PURCHASE_CANCELED"]:
        if email in db_alunos:
            # Marca como cancelado no banco
            db_alunos[email]['status'] = 'cancelled'
            
            # Recupera dados para executar a segurança
            telegram_id = db_alunos[email].get('telegram_id')
            link_pendente = db_alunos[email].get('invite_link')
            
            bot = Bot(token=TOKEN_TELEGRAM)

            # 1. Tenta EXPULSAR (Se já entrou)
            if telegram_id:
                try:
                    await bot.ban_chat_member(chat_id=GRUPO_ID, user_id=telegram_id)
                    await bot.unban_chat_member(chat_id=GRUPO_ID, user_id=telegram_id)
                    print(f"🚫 REMOVIDO: Usuário {telegram_id} removido.")
                except Exception as e:
                    print(f"Aviso: Não foi possível remover (talvez não esteja no grupo ainda). Erro: {e}")

            # 2. Tenta REVOGAR O LINK (Se ainda não entrou)
            if link_pendente:
                try:
                    await bot.revoke_chat_invite_link(chat_id=GRUPO_ID, invite_link=link_pendente)
                    print(f"🔒 LINK REVOGADO: O convite {link_pendente} foi cancelado.")
                except Exception as e:
                    print(f"Aviso: Erro ao revogar link: {e}")

    return {"status": "received"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)