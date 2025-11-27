import asyncio
import json
import os
from contextlib import asynccontextmanager
import psycopg2
from psycopg2.extras import Json
from fastapi import FastAPI, Request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import uvicorn

# --- 1. CONFIGURAÇÕES ---
TOKEN_TELEGRAM = os.getenv("TOKEN_TELEGRAM", "SEU_TOKEN_AQUI")
GRUPO_ID = int(os.getenv("GRUPO_ID", "-1003394118030")) 
DATABASE_URL = os.getenv("DATABASE_URL") 

# --- 2. FUNÇÕES DE BANCO DE DADOS ---
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS alunos (
                email TEXT PRIMARY KEY,
                data JSONB
            );
        """)
        conn.commit()
        cur.close()
        conn.close()
        print("✅ Banco de Dados Conectado.")
    except Exception as e:
        print(f"❌ Erro DB Init: {e}")

def carregar_aluno(email):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT data FROM alunos WHERE email = %s", (email,))
        result = cur.fetchone()
        cur.close()
        conn.close()
        if result:
            return result[0]
        return None
    except Exception as e:
        print(f"Erro DB Load: {e}")
        return None

def salvar_aluno(email, dados_dict):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO alunos (email, data) 
            VALUES (%s, %s)
            ON CONFLICT (email) 
            DO UPDATE SET data = EXCLUDED.data;
        """, (email, Json(dados_dict)))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Erro DB Save: {e}")

# --- 3. LÓGICA DO TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Olá! Sou o guardião do grupo exclusivo de Telegram da Escola de Impermeabilização.\n"
        "Se tiver quaisquer problemas comigo, entre em contato conosco por um de nossos canais, enviando o email da assinatura e o comprovante de inscrição.\n"
        "Digite o **email** usado na compra para liberar ou validar seu acesso."
    )

async def receber_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email_usuario = update.message.text.lower().strip()
    novo_user_id = update.effective_user.id
    
    aluno = carregar_aluno(email_usuario)

    # Verifica se tem PELO MENOS UM produto ativo
    produtos_ativos = aluno.get('active_products', []) if aluno else []
    
    if aluno and len(produtos_ativos) > 0:
        try:
            # --- PROTEÇÃO DE VÍNCULO MÚLTIPLO ---
            id_antigo = aluno.get('telegram_id')
            link_antigo = aluno.get('invite_link')
            
            # 1. Se o usuário já está vinculado e é o MESMO usuário chamando
            if id_antigo == novo_user_id:
                await update.message.reply_text("✅ Você já possui acesso ativo com este usuário. Verifique se já está no grupo.")
                return # Encerra aqui, não gera link novo para não gastar cota nem criar bagunça

            # 2. Se o usuário mudou (Troca de conta/Empréstimo de senha)
            if id_antigo and id_antigo != novo_user_id:
                try:
                    # Remove o anterior
                    await context.bot.ban_chat_member(chat_id=GRUPO_ID, user_id=id_antigo)
                    await context.bot.unban_chat_member(chat_id=GRUPO_ID, user_id=id_antigo)
                    print(f"♻️ TROCA: {id_antigo} removido para entrada de {novo_user_id}.")
                except Exception as e:
                    print(f"Aviso Kick: {e}")

            # 3. Revoga link anterior (se houver)
            if link_antigo:
                try:
                    await context.bot.revoke_chat_invite_link(chat_id=GRUPO_ID, invite_link=link_antigo)
                except:
                    pass

            # 4. Gera NOVO acesso
            convite = await context.bot.create_chat_invite_link(
                chat_id=GRUPO_ID, 
                member_limit=1, 
                name=f"Aluno {email_usuario}" 
            )
            
            # Salva vínculo novo
            aluno['telegram_id'] = novo_user_id
            aluno['invite_link'] = convite.invite_link
            salvar_aluno(email_usuario, aluno)
            
            await update.message.reply_text(
                f"✅ Acesso Confirmado!\n\n"
                f"Aqui está seu link exclusivo e de **uso único**. Não compartilhe:\n{convite.invite_link}\n\n"
                f"⚠️ **Atenção:** Se você gerar um novo link, este anterior deixará de funcionar imediatamente."
                f"⚠️ **Importante:** Este login desconectou qualquer outro dispositivo que estivesse usando este e-mail no grupo."
            )
            print(f"LOGIN: {email_usuario} vinculado ao ID {novo_user_id}")
            
        except Exception as e:
            await update.message.reply_text("Erro técnico ao gerar acesso.")
            print(f"ERRO: {e}")

    else:
        # Caso não tenha nenhum produto ativo na lista
        await update.message.reply_text("❌ Nenhuma assinatura ativa encontrada para este e-mail. Verifique se o endereço está correto e, se sim, entre em contato conosco enviando seu comprovante de assinatura e endereço de email.")

# --- 4. CONFIGURAÇÃO DO BOT ---
ptb_app = Application.builder().token(TOKEN_TELEGRAM).build()
ptb_app.add_handler(CommandHandler("start", start))
ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receber_email))

# --- 5. LIFESPAN ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 Iniciando Sistema...")
    init_db()
    await ptb_app.initialize()
    await ptb_app.start()
    await ptb_app.updater.start_polling()
    print("🤖 Bot ONLINE!")
    yield 
    print("🛑 Parando Sistema...")
    await ptb_app.updater.stop()
    await ptb_app.stop()
    await ptb_app.shutdown()

app = FastAPI(lifespan=lifespan)

# --- 6. WEBHOOK HOTMART (LÓGICA MULTI-PRODUTO) ---
@app.post("/webhook")
async def hotmart_webhook(request: Request):
    dados = await request.json()
    
    # Extração de dados
    evento = dados.get("event")
    data = dados.get("data", {})
    buyer = data.get("buyer", {})
    product = data.get("product", {}) # Hotmart manda dados do produto aqui
    
    email = buyer.get("email", "").lower()
    produto_id = str(product.get("id", "0")) # ID numérico do curso (ex: 123456)

    if not email:
        return {"status": "ignored"}

    print(f"📥 Hotmart: {evento} | Produto: {produto_id} | Email: {email}")

    # Carrega estado atual ou cria vazio
    aluno = carregar_aluno(email)
    if not aluno:
        aluno = {"telegram_id": None, "invite_link": None, "active_products": []}
    
    lista_produtos = set(aluno.get('active_products', [])) # Usa SET para evitar duplicatas

    if evento == "PURCHASE_APPROVED":
        # ADICIONA o produto à lista
        lista_produtos.add(produto_id)
        aluno['active_products'] = list(lista_produtos)
        salvar_aluno(email, aluno)
        print(f"✅ COMPRA: {email} agora tem os produtos: {aluno['active_products']}")
    
    elif evento in ["SUBSCRIPTION_CANCELLATION", "REFUNDED", "PURCHASE_CANCELED"]:
        # REMOVE o produto da lista
        if produto_id in lista_produtos:
            lista_produtos.remove(produto_id)
        
        aluno['active_products'] = list(lista_produtos)
        salvar_aluno(email, aluno)
        
        # VERIFICA SE AINDA SOBROU ALGUMA COISA
        if len(lista_produtos) == 0:
            # Lista vazia -> EXPULSAR AGORA
            telegram_id = aluno.get('telegram_id')
            link_pendente = aluno.get('invite_link')
            
            print(f"🚫 SEM ACESSOS: {email} perdeu o último produto. Iniciando remoção.")
            
            bot = Bot(token=TOKEN_TELEGRAM)
            if telegram_id:
                try:
                    await bot.ban_chat_member(chat_id=GRUPO_ID, user_id=telegram_id)
                    await bot.unban_chat_member(chat_id=GRUPO_ID, user_id=telegram_id)
                    print(f"👋 REMOVIDO: {telegram_id} removido.")
                except Exception as e:
                    print(f"Erro Ban: {e}")
            
            if link_pendente:
                try:
                    await bot.revoke_chat_invite_link(chat_id=GRUPO_ID, invite_link=link_pendente)
                except:
                    pass
        else:
            print(f"⚠️ MANTIDO: {email} cancelou produto {produto_id}, mas ainda tem {len(lista_produtos)} ativos.")

    return {"status": "received"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
