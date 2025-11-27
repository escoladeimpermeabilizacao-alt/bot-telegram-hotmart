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
        "👋 Olá! Sou o guardião do grupo exclusivo de Telegram da Escola de Impermeabilização.\n"
        "\n"
        "Se tiver quaisquer problemas comigo, entre em contato conosco por um de nossos canais, enviando o email da assinatura e o comprovante de inscrição.\n"
        "\n"
        "Digite o **email** usado na compra para liberar ou validar seu acesso."
    )

async def receber_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email_usuario = update.message.text.lower().strip()
    novo_user_id = update.effective_user.id
    
    aluno = carregar_aluno(email_usuario)
    produtos_ativos = aluno.get('active_products', []) if aluno else []
    
    if aluno and len(produtos_ativos) > 0:
        try:
            id_antigo = aluno.get('telegram_id')
            link_antigo = aluno.get('invite_link')
            
            # Reingresso ou mesmo usuário
            if id_antigo == novo_user_id:
                # Opcional: checar se está no grupo via get_chat_member
                await update.message.reply_text("✅ Você já possui acesso ativo.")
                return 

            # Troca de Conta (Kick no antigo)
            if id_antigo and id_antigo != novo_user_id:
                try:
                    await context.bot.ban_chat_member(chat_id=GRUPO_ID, user_id=id_antigo)
                    await context.bot.unban_chat_member(chat_id=GRUPO_ID, user_id=id_antigo)
                    print(f"♻️ TROCA: {id_antigo} removido.")
                except Exception:
                    pass

            if link_antigo:
                try:
                    await context.bot.revoke_chat_invite_link(chat_id=GRUPO_ID, invite_link=link_antigo)
                except:
                    pass

            convite = await context.bot.create_chat_invite_link(
                chat_id=GRUPO_ID, 
                member_limit=1, 
                name=f"Aluno {email_usuario}" 
            )
            
            aluno['telegram_id'] = novo_user_id
            aluno['invite_link'] = convite.invite_link
            salvar_aluno(email_usuario, aluno)
            
            await update.message.reply_text(
                f"✅ Acesso Confirmado!\n\n"
                f"Aqui está seu link exclusivo e de **uso único**. Não compartilhe:\n{convite.invite_link}\n\n"
                f"⚠️ **Atenção:** Se você gerar um novo link, este anterior deixará de funcionar imediatamente."
                f"⚠️ **Importante:** Este login desconectou qualquer outro dispositivo que estivesse usando este e-mail no grupo."
            )
            
        except Exception as e:
            await update.message.reply_text("Erro técnico.")
            print(f"ERRO: {e}")

    else:
        await update.message.reply_text("❌ Nenhuma assinatura ativa encontrada.")

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
    try:
        await ptb_app.bot.delete_webhook(drop_pending_updates=True)
    except:
        pass
    await ptb_app.start()
    await ptb_app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    print("🤖 Bot ONLINE!")
    yield 
    print("🛑 Parando Sistema...")
    if ptb_app.updater.running:
        await ptb_app.updater.stop()
    if ptb_app.running:
        await ptb_app.stop()
    await ptb_app.shutdown()

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def health_check():
    return {"status": "ok", "message": "Bot is awake!"}

# --- 6. WEBHOOK HOTMART (LÓGICA SEQUENCIAL DE TESTE) ---
@app.post("/webhook")
async def hotmart_webhook(request: Request):
    dados = await request.json()
    evento = dados.get("event")
    data = dados.get("data", {})
    buyer = data.get("buyer", {})
    product = data.get("product", {}) 
    
    email = buyer.get("email", "").lower()
    produto_id = str(product.get("id", "0"))

    if not email:
        return {"status": "ignored"}

    # Carrega o aluno para ver o que ele já tem
    aluno = carregar_aluno(email)
    if not aluno:
        aluno = {"telegram_id": None, "invite_link": None, "active_products": []}
    
    lista_produtos = set(aluno.get('active_products', []))

    # --- LÓGICA ESPECIAL PARA O EMAIL DE TESTE DA HOTMART ---
    # Se for o email "testeComprador...", a gente ignora o ID 0 e inventa IDs sequenciais
    if "testecomprador" in email:
        print(f"🧪 MODO TESTE DETECTADO para {email}")
        
        if evento == "PURCHASE_APPROVED":
            if "1001" not in lista_produtos:
                produto_id = "1001"
            else:
                produto_id = "2002"
                
        elif evento in ["SUBSCRIPTION_CANCELLATION", "PURCHASE_REFUNDED", "PURCHASE_CANCELED", "COMPLETED"]:
            # === EXTERMINADOR DE ZUMBI ===
            # Se tiver um '0' preso lá, a gente apaga ele na força bruta agora
            if '0' in lista_produtos:
                lista_produtos.remove('0')
                print("💀 Zumbi '0' removido da lista.")
            # =============================

            if "2002" in lista_produtos:
                produto_id = "2002"
            else:
                produto_id = "1001"
                
        print(f"🧪 ID MÁGICO APLICADO: {produto_id}")
    # ---------------------------------------------------------

    print(f"📥 Processando: {evento} | ID Final: {produto_id} | Email: {email}")

    if evento == "PURCHASE_APPROVED":
        lista_produtos.add(produto_id)
        aluno['active_products'] = list(lista_produtos)
        salvar_aluno(email, aluno)
        print(f"✅ ATIVO. Produtos atuais: {aluno['active_products']}")
    
    elif evento in ["SUBSCRIPTION_CANCELLATION", "PURCHASE_REFUNDED", "PURCHASE_CANCELED", "COMPLETED"]:
        if produto_id in lista_produtos:
            lista_produtos.remove(produto_id)
        
        aluno['active_products'] = list(lista_produtos)
        
        # Se a lista ficou vazia após remover, faz a expulsão
        if len(lista_produtos) == 0:
            telegram_id = aluno.get('telegram_id')
            link_pendente = aluno.get('invite_link')
            
            # Limpa o registro para permitir reingresso futuro
            aluno['telegram_id'] = None
            aluno['invite_link'] = None
            salvar_aluno(email, aluno)

            print(f"🚫 SEM ACESSOS: Removendo usuário do grupo.")
            
            bot = Bot(token=TOKEN_TELEGRAM)
            if telegram_id:
                try:
                    await bot.ban_chat_member(chat_id=GRUPO_ID, user_id=telegram_id)
                    await bot.unban_chat_member(chat_id=GRUPO_ID, user_id=telegram_id)
                except Exception as e:
                    print(f"Erro Ban: {e}")
            
            if link_pendente:
                try:
                    await bot.revoke_chat_invite_link(chat_id=GRUPO_ID, invite_link=link_pendente)
                except:
                    pass
        else:
            salvar_aluno(email, aluno)
            print(f"⚠️ MANTIDO. Ainda possui produtos: {aluno['active_products']}")

    return {"status": "received"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)




