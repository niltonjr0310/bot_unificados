"""
Três bots do Telegram (Lembretes, Finanças e Lista de Mercado) rodando
dentro de um único processo/serviço, para caber no plano gratuito do Render.

Cada bot continua 100% separado no Telegram: token próprio, comandos
próprios, conversa própria. A única coisa que muda é a hospedagem: os três
rodam juntos "por baixo dos panos".

Variáveis de ambiente necessárias:
  BOT_TOKEN_LEMBRETES
  BOT_TOKEN_FINANCAS
  BOT_TOKEN_MERCADO
"""

import os
import re
import json
import logging
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

FUSO = ZoneInfo("America/Sao_Paulo")


# ---------------------------------------------------------------------------
# Servidor de "ping" único para os três, mantém o serviço acordado no Render
# ---------------------------------------------------------------------------
class _PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass


def _start_ping_server():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), _PingHandler).serve_forever()


# ===========================================================================
# BOT 1: LEMBRETES
# ===========================================================================
INTERVALO_APOS_HORARIO_MIN = 5
active_reminders = {}
_next_reminder_id = 1


async def _lem_enviar_lembrete(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    reminder_id = job.data["reminder_id"]
    text = job.data["text"]
    chat_id = job.chat_id
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Feito ✅", callback_data=f"done:{reminder_id}")]]
    )
    await context.bot.send_message(
        chat_id=chat_id, text=f"🔔 Lembrete: {text}", reply_markup=keyboard
    )


async def _lem_iniciar_repeticao_apos_horario(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    reminder_id = job.data["reminder_id"]
    text = job.data["text"]
    chat_id = job.chat_id
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Feito ✅", callback_data=f"done:{reminder_id}")]]
    )
    await context.bot.send_message(
        chat_id=chat_id, text=f"🔔 Lembrete: {text}", reply_markup=keyboard
    )
    novo_job = context.job_queue.run_repeating(
        _lem_enviar_lembrete,
        interval=INTERVALO_APOS_HORARIO_MIN * 60,
        first=INTERVALO_APOS_HORARIO_MIN * 60,
        chat_id=chat_id,
        data={"reminder_id": reminder_id, "text": text},
        name=str(reminder_id),
    )
    if reminder_id in active_reminders:
        active_reminders[reminder_id]["job"] = novo_job


def _lem_parse_horario(texto: str):
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", texto)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


async def lem_lembrar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global _next_reminder_id

    if len(context.args) < 2:
        await update.message.reply_text(
            "Use assim:\n"
            "/lembrar <minutos> <mensagem> - repete a partir de agora\n"
            "/lembrar <HH:MM> <mensagem> - dispara em um horário fixo\n\n"
            "Exemplos:\n/lembrar 30 Beber agua\n/lembrar 11:30 Ligar som"
        )
        return

    primeiro_arg = context.args[0]
    texto = " ".join(context.args[1:])
    chat_id = update.effective_chat.id
    reminder_id = _next_reminder_id
    _next_reminder_id += 1

    horario = _lem_parse_horario(primeiro_arg)

    if horario is not None:
        hora, minuto = horario
        agora = datetime.now(FUSO)
        alvo = agora.replace(hour=hora, minute=minuto, second=0, microsecond=0)
        if alvo <= agora:
            alvo += timedelta(days=1)

        job = context.job_queue.run_once(
            _lem_iniciar_repeticao_apos_horario,
            when=alvo,
            chat_id=chat_id,
            data={"reminder_id": reminder_id, "text": texto},
            name=str(reminder_id),
        )
        active_reminders[reminder_id] = {"job": job, "text": texto, "chat_id": chat_id}
        await update.message.reply_text(
            f"✅ Lembrete #{reminder_id} criado: \"{texto}\" às {hora:02d}:{minuto:02d}, "
            f"repetindo a cada {INTERVALO_APOS_HORARIO_MIN} min depois disso até você "
            f"apertar Feito."
        )
        return

    try:
        minutos = float(primeiro_arg)
    except ValueError:
        await update.message.reply_text(
            "O primeiro valor precisa ser um número de minutos (ex: 30) "
            "ou um horário (ex: 11:30)."
        )
        return

    job = context.job_queue.run_repeating(
        _lem_enviar_lembrete,
        interval=minutos * 60,
        first=1,
        chat_id=chat_id,
        data={"reminder_id": reminder_id, "text": texto},
        name=str(reminder_id),
    )
    active_reminders[reminder_id] = {"job": job, "text": texto, "chat_id": chat_id}
    await update.message.reply_text(
        f"✅ Lembrete #{reminder_id} criado: \"{texto}\" a cada {minutos} min, "
        f"até você apertar Feito."
    )


async def lem_listar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    itens = [
        f"#{rid} - {info['text']}"
        for rid, info in active_reminders.items()
        if info["chat_id"] == chat_id
    ]
    await update.message.reply_text(
        "Lembretes ativos:\n" + "\n".join(itens) if itens else "Nenhum lembrete ativo."
    )


async def lem_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Use assim: /cancelar <id>")
        return
    try:
        reminder_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("O id precisa ser um número.")
        return
    info = active_reminders.pop(reminder_id, None)
    if info:
        info["job"].schedule_removal()
        await update.message.reply_text(f"❌ Lembrete #{reminder_id} cancelado.")
    else:
        await update.message.reply_text("Não achei esse lembrete.")


async def lem_botao_feito(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, reminder_id_str = query.data.split(":")
    reminder_id = int(reminder_id_str)
    info = active_reminders.pop(reminder_id, None)
    if info:
        info["job"].schedule_removal()
        await query.edit_message_text(f"✅ Concluído: {info['text']}")
    else:
        await query.edit_message_text("Esse lembrete já não está mais ativo.")


async def lem_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Oi! Eu sou seu bot de lembretes.\n\n"
        "/lembrar <minutos> <mensagem> - repete a partir de agora\n"
        "/lembrar <HH:MM> <mensagem> - dispara em um horário fixo\n"
        "/listar - mostra lembretes ativos\n"
        "/cancelar <id> - cancela um lembrete"
    )


# ===========================================================================
# BOT 2: FINANÇAS
# ===========================================================================
FIN_DATA_FILE = os.path.join(os.path.dirname(__file__), "gastos.json")


def _fin_load():
    if not os.path.exists(FIN_DATA_FILE):
        return {}
    with open(FIN_DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _fin_save(data):
    with open(FIN_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _fin_formatar_reais(valor: float) -> str:
    texto = f"{valor:,.2f}"
    texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


async def fin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Oi! Eu guardo seus gastos.\n\n"
        "/gasto <valor> <descrição> - registra um gasto\n"
        "Exemplo: /gasto 25.90 Almoço\n\n"
        "/extrato - mostra a lista de gastos e o total\n"
        "/zerar - apaga todos os gastos registrados"
    )


async def fin_gasto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text(
            "Use assim: /gasto <valor> <descrição>\nExemplo: /gasto 25.90 Almoço"
        )
        return
    try:
        valor = float(context.args[0].replace(",", "."))
    except ValueError:
        await update.message.reply_text(
            "O valor precisa ser um número. Exemplo: /gasto 25.90 Almoço"
        )
        return

    descricao = " ".join(context.args[1:])
    chat_id = str(update.effective_chat.id)
    data = _fin_load()
    data.setdefault(chat_id, [])
    data[chat_id].append(
        {"valor": valor, "descricao": descricao, "data": datetime.now(FUSO).strftime("%d/%m %H:%M")}
    )
    _fin_save(data)
    await update.message.reply_text(f"✅ Registrado: {descricao} - {_fin_formatar_reais(valor)}")


async def fin_extrato(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    gastos = _fin_load().get(chat_id, [])
    if not gastos:
        await update.message.reply_text("Nenhum gasto registrado ainda.")
        return
    linhas = [f"{g['data']} - {g['descricao']}: {_fin_formatar_reais(g['valor'])}" for g in gastos]
    total = sum(g["valor"] for g in gastos)
    mensagem = "📋 Extrato:\n\n" + "\n".join(linhas) + f"\n\n💰 Total: {_fin_formatar_reais(total)}"
    await update.message.reply_text(mensagem)


async def fin_zerar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    data = _fin_load()
    data[chat_id] = []
    _fin_save(data)
    await update.message.reply_text("🗑️ Todos os gastos foram apagados.")


# ===========================================================================
# BOT 3: LISTA DE MERCADO
# ===========================================================================
MER_DATA_FILE = os.path.join(os.path.dirname(__file__), "lista.json")


def _mer_load():
    if not os.path.exists(MER_DATA_FILE):
        return {}
    with open(MER_DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _mer_save(data):
    with open(MER_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _mer_get_chat_data(data, chat_id):
    return data.setdefault(chat_id, {"next_id": 1, "itens": {}})


def _mer_montar_teclado(chat_data):
    botoes = [
        [InlineKeyboardButton(f"{nome} — Comprado ✅", callback_data=f"comprado:{item_id}")]
        for item_id, nome in chat_data["itens"].items()
    ]
    return InlineKeyboardMarkup(botoes)


async def mer_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Oi! Eu guardo sua lista de mercado.\n\n"
        "/item <nome> - adiciona um ou mais itens (separe por vírgula)\n"
        "Exemplo: /item arroz, feijão, leite\n\n"
        "/lista - mostra a lista com botão de Comprado\n"
        "/limpar - apaga a lista inteira"
    )


async def mer_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Use assim: /item <nome>\nExemplo: /item arroz, feijão, leite"
        )
        return
    nomes = [n.strip() for n in " ".join(context.args).split(",") if n.strip()]
    chat_id = str(update.effective_chat.id)
    data = _mer_load()
    chat_data = _mer_get_chat_data(data, chat_id)
    for nome in nomes:
        item_id = str(chat_data["next_id"])
        chat_data["next_id"] += 1
        chat_data["itens"][item_id] = nome
    _mer_save(data)
    plural = "itens adicionados" if len(nomes) > 1 else "item adicionado"
    await update.message.reply_text(f"✅ {plural}: {', '.join(nomes)}\nUse /lista para ver tudo.")


async def mer_lista(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    data = _mer_load()
    chat_data = _mer_get_chat_data(data, chat_id)
    if not chat_data["itens"]:
        await update.message.reply_text("Sua lista está vazia. Use /item <nome> para adicionar.")
        return
    await update.message.reply_text("🛒 Lista de mercado:", reply_markup=_mer_montar_teclado(chat_data))


async def mer_limpar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    data = _mer_load()
    chat_data = _mer_get_chat_data(data, chat_id)
    chat_data["itens"] = {}
    _mer_save(data)
    await update.message.reply_text("🗑️ Lista apagada.")


async def mer_botao_comprado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, item_id = query.data.split(":")
    chat_id = str(update.effective_chat.id)
    data = _mer_load()
    chat_data = _mer_get_chat_data(data, chat_id)
    chat_data["itens"].pop(item_id, None)
    _mer_save(data)
    if chat_data["itens"]:
        await query.edit_message_text("🛒 Lista de mercado:", reply_markup=_mer_montar_teclado(chat_data))
    else:
        await query.edit_message_text("🛒 Lista de mercado:\n\nTudo comprado! 🎉")


# ===========================================================================
# EXECUÇÃO: os três bots rodam juntos, cada um com seu próprio token
# ===========================================================================
def _build_lembretes_app(token):
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", lem_start))
    app.add_handler(CommandHandler("lembrar", lem_lembrar))
    app.add_handler(CommandHandler("listar", lem_listar))
    app.add_handler(CommandHandler("cancelar", lem_cancelar))
    app.add_handler(CallbackQueryHandler(lem_botao_feito, pattern=r"^done:"))
    return app


def _build_financas_app(token):
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", fin_start))
    app.add_handler(CommandHandler("gasto", fin_gasto))
    app.add_handler(CommandHandler("extrato", fin_extrato))
    app.add_handler(CommandHandler("zerar", fin_zerar))
    return app


def _build_mercado_app(token):
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", mer_start))
    app.add_handler(CommandHandler("item", mer_item))
    app.add_handler(CommandHandler("lista", mer_lista))
    app.add_handler(CommandHandler("limpar", mer_limpar))
    app.add_handler(CallbackQueryHandler(mer_botao_comprado, pattern=r"^comprado:"))
    return app


def _run_em_thread(app, nome):
    """Roda um Application em segundo plano (thread própria)."""
    try:
        logger.info(f"Iniciando bot: {nome}")
        app.run_polling(stop_signals=None)
    except Exception:
        logger.exception(f"Bot {nome} caiu com um erro")


def main():
    threading.Thread(target=_start_ping_server, daemon=True).start()

    token_lembretes = os.environ.get("BOT_TOKEN_LEMBRETES")
    token_financas = os.environ.get("BOT_TOKEN_FINANCAS")
    token_mercado = os.environ.get("BOT_TOKEN_MERCADO")

    if not all([token_lembretes, token_financas, token_mercado]):
        raise RuntimeError(
            "Defina BOT_TOKEN_LEMBRETES, BOT_TOKEN_FINANCAS e BOT_TOKEN_MERCADO "
            "nas variáveis de ambiente."
        )

    app_lembretes = _build_lembretes_app(token_lembretes)
    app_financas = _build_financas_app(token_financas)
    app_mercado = _build_mercado_app(token_mercado)

    # os dois primeiros rodam em threads separadas; o terceiro roda na
    # thread principal (assim o processo não termina sozinho)
    threading.Thread(
        target=_run_em_thread, args=(app_lembretes, "lembretes"), daemon=True
    ).start()
    threading.Thread(
        target=_run_em_thread, args=(app_financas, "finanças"), daemon=True
    ).start()

    logger.info("Os três bots foram iniciados.")
    _run_em_thread(app_mercado, "mercado")


if __name__ == "__main__":
    main()
