import os
import json
import sqlite3
import threading
import time
import random
import uuid
from typing import Dict, List, Optional, Tuple

import telebot
from telebot import types


BOT_TOKEN = "Token_Bot_Telegram"
bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)
DB_PATH = "data.db" # مسیر دیتابیس
INACTIVITY_SECONDS = 5 * 60
STALE_CLEANUP_SECONDS = 24 * 3600
LOCK = threading.Lock()
GAME_LOCKS: Dict[str, threading.Lock] = {}

EMOJI_X = "❌"
EMOJI_O = "⭕"
EMOJI_EMPTY = "⬜️"
WIN_ANIM = ["✨", "💫", "🌟"]


def init_db():
    with LOCK:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS games (
            game_id TEXT PRIMARY KEY,
            chat_id INTEGER,
            message_id INTEGER,
            state_json TEXT,
            last_activity INTEGER
        )
        """
        )
        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS stats (
            user_id INTEGER PRIMARY KEY,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            draws INTEGER DEFAULT 0,
            win_streak INTEGER DEFAULT 0,
            best_streak INTEGER DEFAULT 0
        )
        """
        )
        conn.commit()
        conn.close()


def save_game(game_id: str, chat_id: int, message_id: Optional[int], state: Dict):
    with LOCK:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        now = int(time.time())
        j = json.dumps(state, ensure_ascii=False)
        cur.execute(
            "REPLACE INTO games (game_id, chat_id, message_id, state_json, last_activity) VALUES (?,?,?,?,?)",
            (game_id, chat_id, message_id or 0, j, now),
        )
        conn.commit()
        conn.close()


def load_game(game_id: str) -> Optional[Tuple[int, Optional[int], Dict, int]]:
    with LOCK:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT chat_id, message_id, state_json, last_activity FROM games WHERE game_id=?", (game_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return None
        chat_id, message_id, state_json, last_activity = row
        return chat_id, message_id if message_id != 0 else None, json.loads(state_json), last_activity


def delete_game(game_id: str):
    with LOCK:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("DELETE FROM games WHERE game_id=?", (game_id,))
        conn.commit()
        conn.close()


def update_last_activity(game_id: str):
    with LOCK:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("UPDATE games SET last_activity=? WHERE game_id=?", (int(time.time()), game_id))
        conn.commit()
        conn.close()


WIN_LINES = [
    (0, 1, 2),
    (3, 4, 5),
    (6, 7, 8),
    (0, 3, 6),
    (1, 4, 7),
    (2, 5, 8),
    (0, 4, 8),
    (2, 4, 6),
]


def new_game(game_type: str, creator_id: int, opponent_id: Optional[int] = None, ai_difficulty: Optional[str] = None) -> Dict:
    state = {
        "board": [""] * 9,
        "current_player": "X",
        "game_type": game_type,
        "players": {"X": creator_id, "O": opponent_id if opponent_id else None},
        "ai_difficulty": ai_difficulty,
        "history": [],
        "finished": False,
        "winner": None,
        "_id": None,
        "messages": {},
    }
    return state


def generate_game_id() -> str:
    return uuid.uuid4().hex[:12]


def get_game_lock(game_id: str) -> threading.Lock:
    with LOCK:
        if game_id not in GAME_LOCKS:
            GAME_LOCKS[game_id] = threading.Lock()
        return GAME_LOCKS[game_id]


def who_is_player(state: Dict, user_id: int) -> Optional[str]:
    for k, v in state["players"].items():
        if v == user_id:
            return k
    return None


def check_winner(board: List[str]) -> Optional[Tuple[str, List[int]]]:
    for a, b, c in WIN_LINES:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a], [a, b, c]
    return None


def is_draw(board: List[str]) -> bool:
    return all(cell in ["X", "O"] for cell in board)


# ---------- UI helpers ----------
def safe_get_username(user_id: Optional[int]) -> str:
    if not isinstance(user_id, int):
        return "منتظر بازیکن"
    try:
        chat = bot.get_chat(user_id)
        if getattr(chat, "first_name", None):
            return chat.first_name
        if getattr(chat, "username", None):
            return f"@{chat.username}"
    except Exception:
        pass
    return f"کاربر #{user_id}"


def render_board(state: Dict, highlight: Optional[List[int]] = None, anim_emoji: str = None) -> Tuple[str, types.InlineKeyboardMarkup]:
    board = state["board"]
    turn = state["current_player"]
    x_name = safe_get_username(state["players"].get("X"))
    o_name = safe_get_username(state["players"].get("O"))
    
    header = (
        f"🎮 بازی دوز | نوبت: {'بازیکن X' if turn == 'X' else 'بازیکن O'}\n"
        f"🔷 بازیکن X: {x_name}\n"
        f"🔶 بازیکن O: {o_name}\n"
        f"📊 حرکات: {len(state.get('history', []))}"
    )
    
    kb = types.InlineKeyboardMarkup(row_width=3)
    btns = []
    for i in range(9):
        val = board[i]
        if val == "X":
            label = EMOJI_X
        elif val == "O":
            label = EMOJI_O
        else:
            label = EMOJI_EMPTY
        
        if highlight and i in highlight:
            label = anim_emoji or random.choice(WIN_ANIM)
        cb = f"move_{state.get('_id','')}|{i}"
        btns.append(types.InlineKeyboardButton(label, callback_data=cb))
    
    kb.row(btns[0], btns[1], btns[2])
    kb.row(btns[3], btns[4], btns[5])
    kb.row(btns[6], btns[7], btns[8])

    action_row = []
    action_row.append(types.InlineKeyboardButton("🔄 ریست بازی", callback_data=f"restart_{state.get('_id','')}"))
    action_row.append(types.InlineKeyboardButton("🏳️ تسلیم", callback_data=f"forfeit_{state.get('_id','')}"))
    action_row.append(types.InlineKeyboardButton("🔁 رفرش بورد", callback_data=f"refresh_{state.get('_id','')}"))
    kb.row(*action_row)

    if state.get("game_type") == "pvp" and not state.get("finished"):
        try:
            me = bot.get_me()
            gid = state.get("_id", "")
            invite_url = f"https://t.me/{me.username}?start=join_{gid}"
            kb.row(types.InlineKeyboardButton("📩 دعوت از دوست", url=invite_url))
        except Exception:
            pass

    return header, kb



def minimax_ab(board: List[str], depth: int, is_max: bool, ai_player: str, human_player: str, alpha: int, beta: int) -> Tuple[int, Optional[int]]:
    winner = check_winner(board)
    if winner:
        winp = winner[0]
        if winp == ai_player:
            return 10 + depth, None
        elif winp == human_player:
            return -10 - depth, None
    if is_draw(board) or depth == 0:
        return 0, None

    best_move = None
    if is_max:
        value = -9999
        for i in range(9):
            if not board[i]:
                board[i] = ai_player
                v, _ = minimax_ab(board, depth - 1, False, ai_player, human_player, alpha, beta)
                board[i] = ""
                if v > value:
                    value = v
                    best_move = i
                alpha = max(alpha, value)
                if alpha >= beta:
                    break
        return value, best_move
    else:
        value = 9999
        for i in range(9):
            if not board[i]:
                board[i] = human_player
                v, _ = minimax_ab(board, depth - 1, True, ai_player, human_player, alpha, beta)
                board[i] = ""
                if v < value:
                    value = v
                    best_move = i
                beta = min(beta, value)
                if alpha >= beta:
                    break
        return value, best_move


def ai_choose_move(state: Dict) -> int:
    board = state["board"][:]
    difficulty = state.get("ai_difficulty", "medium")
    valid = [i for i, v in enumerate(board) if v == ""]
    ai_player = "O"
    human_player = "X"
    
    if difficulty == "easy":
        return random.choice(valid)
    elif difficulty == "medium":
        _, move = minimax_ab(board, depth=3, is_max=True, ai_player=ai_player, human_player=human_player, alpha=-9999, beta=9999)
        return move if move is not None else random.choice(valid)
    elif difficulty == "hard":
        depth = sum(1 for c in board if c == "")
        _, move = minimax_ab(board, depth=depth, is_max=True, ai_player=ai_player, human_player=human_player, alpha=-9999, beta=9999)
        return move if move is not None else random.choice(valid)
    else:
        _, move = minimax_ab(board, depth=3, is_max=True, ai_player=ai_player, human_player=human_player, alpha=-9999, beta=9999)
        return move if move is not None else random.choice(valid)


# ---------- stats ----------
def get_or_create_stats(user_id: int) -> Dict:
    with LOCK:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT wins, losses, draws, win_streak, best_streak FROM stats WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        if not row:
            cur.execute(
                "INSERT OR REPLACE INTO stats (user_id, wins, losses, draws, win_streak, best_streak) VALUES (?,?,?,?,?,?)",
                (user_id, 0, 0, 0, 0, 0),
            )
            conn.commit()
            stats = {"wins": 0, "losses": 0, "draws": 0, "win_streak": 0, "best_streak": 0}
        else:
            stats = {"wins": row[0], "losses": row[1], "draws": row[2], "win_streak": row[3], "best_streak": row[4]}
        conn.close()
        return stats


def update_stats_on_result(state: Dict, result: str):
    players = state["players"]
    if result == "draw":
        for p in ("X", "O"):
            uid = players.get(p)
            if isinstance(uid, int):
                stats = get_or_create_stats(uid)
                stats["draws"] += 1
                stats["win_streak"] = 0
                with LOCK:
                    conn = sqlite3.connect(DB_PATH)
                    cur = conn.cursor()
                    cur.execute("UPDATE stats SET draws=?, win_streak=? WHERE user_id=?", (stats["draws"], stats["win_streak"], uid))
                    conn.commit()
                    conn.close()
        return
    
    winner = result
    loser = "O" if winner == "X" else "X"
    winner_id = players.get(winner)
    loser_id = players.get(loser)
    
    if isinstance(winner_id, int):
        stats_w = get_or_create_stats(winner_id)
        stats_w["wins"] += 1
        stats_w["win_streak"] += 1
        if stats_w["win_streak"] > stats_w["best_streak"]:
            stats_w["best_streak"] = stats_w["win_streak"]
        with LOCK:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("UPDATE stats SET wins=?, win_streak=?, best_streak=? WHERE user_id=?", (stats_w["wins"], stats_w["win_streak"], stats_w["best_streak"], winner_id))
            conn.commit()
            conn.close()
    
    if isinstance(loser_id, int):
        stats_l = get_or_create_stats(loser_id)
        stats_l["losses"] += 1
        stats_l["win_streak"] = 0
        with LOCK:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("UPDATE stats SET losses=?, win_streak=? WHERE user_id=?", (stats_l["losses"], stats_l["win_streak"], loser_id))
            conn.commit()
            conn.close()


def finish_game_and_announce(game_id: str, win_result: str, highlight: Optional[List[int]] = None):
    loaded = load_game(game_id)
    if not loaded:
        return
    chat_id, message_id, state, _ = loaded
    state["finished"] = True
    state["winner"] = win_result
    save_game(game_id, chat_id, message_id, state)
    update_stats_on_result(state, win_result)
    players = state["players"]

    def anim():
        try:
            for frame in range(4):
                anim_emoji = WIN_ANIM[frame % len(WIN_ANIM)]
                header, markup = render_board(state, highlight=highlight, anim_emoji=anim_emoji)
                bot.edit_message_text(
                    f"{header}\n\n🎉 بازیکن {'X' if win_result == 'X' else 'O'} برنده شد! {anim_emoji}",
                    chat_id,
                    message_id,
                    reply_markup=markup
                )
                time.sleep(0.45)
                
                header, markup = render_board(state, highlight=None)
                bot.edit_message_text(header, chat_id, message_id, reply_markup=markup)
                time.sleep(0.25)
            
            if win_result == "draw":
                header, markup = render_board(state)
                final_text = f"{header}\n\n🤝 بازی مساوی شد!"
            else:
                header, markup = render_board(state, highlight=highlight)
                winner_id = players.get(win_result)
                streak_text = ""
                if isinstance(winner_id, int):
                    stats = get_or_create_stats(winner_id)
                    streak_text = f"\n🏆 رکورد برد فعلی: {stats.get('win_streak',0)} | بهترین رکورد: {stats.get('best_streak',0)}"
                final_text = f"{header}\n\n🎉 بازیکن {'X' if win_result == 'X' else 'O'} برنده شد! {random.choice(WIN_ANIM)}{streak_text}"
            
            bot.edit_message_text(final_text, chat_id, message_id, reply_markup=markup)
        except Exception as e:
            print(f"Animation error: {e}")
        finally:
            delete_game(game_id)

    threading.Thread(target=anim).start()



def inactivity_watcher():
    while True:
        with LOCK:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("SELECT game_id, chat_id, message_id, state_json, last_activity FROM games")
            rows = cur.fetchall()
            conn.close()
        
        now = int(time.time())
        for row in rows:
            game_id, chat_id, message_id, state_json, last_activity = row
            st = json.loads(state_json)
            
            if st.get("finished"):
                if now - last_activity > STALE_CLEANUP_SECONDS:
                    delete_game(game_id)
                continue
            
            if now - last_activity > INACTIVITY_SECONDS:
                cur_player = st.get("current_player")
                other = "O" if cur_player == "X" else "X"
                finish_game_and_announce(game_id, other)
                try:
                    bot.send_message(
                        chat_id,
                        f"⏰ بازی به دلیل عدم فعالیت بیش از {INACTIVITY_SECONDS//60} دقیقه خاتمه یافت.\n"
                        f"بازیکن {other} به دلیل انصراف حریف برنده اعلام شد."
                    )
                except Exception:
                    pass
        
        time.sleep(30)



@bot.callback_query_handler(func=lambda call: call.data.startswith("forfeit_"))
def handle_forfeit_callback(call: types.CallbackQuery):
    try:
        gid = call.data.split("_", 1)[1]
        loaded = load_game(gid)
        if not loaded:
            bot.answer_callback_query(call.id, "بازی مورد نظر پیدا نشد.", show_alert=True)
            return
        
        chat_id, message_id, state, _ = loaded
        user = call.from_user
        role = who_is_player(state, user.id)
        
        if not role:
            bot.answer_callback_query(call.id, "شما در این بازی شرکت ندارید.", show_alert=True)
            return
        
        confirm_kb = types.InlineKeyboardMarkup()
        confirm_kb.row(
            types.InlineKeyboardButton("✅ بله، تسلیم می‌شوم", callback_data=f"confirm_forfeit_{gid}"),
            types.InlineKeyboardButton("❌ لغو", callback_data=f"cancel_{gid}")
        )
        
        bot.edit_message_text(
            f"آیا مطمئن هستید که می‌خواهید تسلیم شوید؟",
            chat_id,
            message_id,
            reply_markup=confirm_kb
        )
        bot.answer_callback_query(call.id)
        
    except Exception as e:
        bot.answer_callback_query(call.id, "خطا در پردازش درخواست.")
        print(f"Forfeit error: {e}")


@bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_forfeit_"))
def handle_confirm_forfeit(call: types.CallbackQuery):
    try:
        gid = call.data.split("_", 2)[2]
        loaded = load_game(gid)
        if not loaded:
            bot.answer_callback_query(call.id, "بازی مورد نظر پیدا نشد.", show_alert=True)
            return
        
        chat_id, message_id, state, _ = loaded
        user = call.from_user
        role = who_is_player(state, user.id)
        
        if role:
            winner = "O" if role == "X" else "X"
            finish_game_and_announce(gid, winner)
            bot.answer_callback_query(call.id, "شما با موفقیت تسلیم شدید.")
        else:
            bot.answer_callback_query(call.id, "شما در این بازی شرکت ندارید.", show_alert=True)
            
    except Exception as e:
        bot.answer_callback_query(call.id, "خطا در پردازش تسلیم‌شدن.")
        print(f"Confirm forfeit error: {e}")


@bot.callback_query_handler(func=lambda call: call.data.startswith("cancel_"))
def handle_cancel(call: types.CallbackQuery):
    try:
        gid = call.data.split("_", 1)[1]
        loaded = load_game(gid)
        if not loaded:
            bot.answer_callback_query(call.id, "بازی مورد نظر پیدا نشد.", show_alert=True)
            return
        
        chat_id, message_id, state, _ = loaded
        header, markup = render_board(state)
        bot.edit_message_text(header, chat_id, message_id, reply_markup=markup)
        bot.answer_callback_query(call.id, "عملیات لغو شد.")
        
    except Exception as e:
        bot.answer_callback_query(call.id, "خطا در لغو عملیات.")
        print(f"Cancel error: {e}")


@bot.callback_query_handler(func=lambda call: call.data.startswith("restart_"))
def handle_restart_callback(call: types.CallbackQuery):
    try:
        gid = call.data.split("_", 1)[1]
        loaded = load_game(gid)
        if not loaded:
            bot.answer_callback_query(call.id, "بازی مورد نظر پیدا نشد.", show_alert=True)
            return
        
        chat_id, message_id, state, _ = loaded
        user = call.from_user
        role = who_is_player(state, user.id)
        
        if not role:
            bot.answer_callback_query(call.id, "فقط بازیکنان می‌توانند بازی را ریست‌کنند.", show_alert=True)
            return
        
        confirm_kb = types.InlineKeyboardMarkup()
        confirm_kb.row(
            types.InlineKeyboardButton("✅ بله، ریست‌کن", callback_data=f"confirm_restart_{gid}"),
            types.InlineKeyboardButton("❌ لغو", callback_data=f"cancel_{gid}")
        )
        
        bot.edit_message_text(
            f"آیا مطمئن هستید که می‌خواهید بازی را ریست‌کنید؟",
            chat_id,
            message_id,
            reply_markup=confirm_kb
        )
        bot.answer_callback_query(call.id)
        
    except Exception as e:
        bot.answer_callback_query(call.id, "خطا در پردازش درخواست.")
        print(f"Restart error: {e}")


@bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_restart_"))
def handle_confirm_restart(call: types.CallbackQuery):
    try:
        gid = call.data.split("_", 2)[2]
        loaded = load_game(gid)
        if not loaded:
            bot.answer_callback_query(call.id, "بازی مورد نظر پیدا نشد.", show_alert=True)
            return
        
        chat_id, message_id, state, _ = loaded
        lock = get_game_lock(gid)
        with lock:
            state["board"] = [""] * 9
            state["current_player"] = "X"
            state["history"] = []
            state["finished"] = False
            state["winner"] = None
            save_game(gid, chat_id, message_id, state)
            header, markup = render_board(state)
            
            try:
                if message_id:
                    bot.edit_message_text("🔄 بازی با موفقیت ریست شد!", chat_id, message_id)
                    bot.edit_message_text(header, chat_id, message_id, reply_markup=markup)
                else:
                    bot.send_message(chat_id, "🔄 بازی ریست شد")
            except Exception:
                pass
            
        bot.answer_callback_query(call.id, "بازی با موفقیت ریست شد.")
        
    except Exception as e:
        bot.answer_callback_query(call.id, "خطا در ریست‌کردن بازی.")
        print(f"Confirm restart error: {e}")



@bot.callback_query_handler(func=lambda call: call.data.startswith("refresh_"))
def handle_refresh_callback(call: types.CallbackQuery):
    try:
        gid = call.data.split("_", 1)[1]
        loaded = load_game(gid)
        if not loaded:
            bot.answer_callback_query(call.id, "بازی پیدا نشد یا منقضی شده.", show_alert=True)
            return
        chat_id, message_id, state, _ = loaded
        header, markup = render_board(state)
        
        msginfo = None
        for p in ["X", "O"]:
            if state.get("messages", {}).get(p, {}).get("chat_id") == call.message.chat.id:
                msginfo = state["messages"][p]
                break
        try:
            if msginfo and msginfo.get("message_id"):
                bot.edit_message_text(header, call.message.chat.id, msginfo["message_id"], reply_markup=markup)
            else:
                bot.send_message(call.message.chat.id, header, reply_markup=markup)
        except Exception:
            bot.send_message(call.message.chat.id, header, reply_markup=markup)
        bot.answer_callback_query(call.id, "بورد به‌روز شد.")
    except Exception as e:
        bot.answer_callback_query(call.id, "خطا در رفرش بورد.")
        print(f"Refresh error: {e}")


@bot.message_handler(commands=["start"])
def cmd_start(message: types.Message):
    user = message.from_user
    payload = None
    
    if message.text and " " in message.text:
        parts = message.text.split(maxsplit=1)
        if len(parts) > 1:
            payload = parts[1]
    
    if payload and payload.startswith("join_"):
        gid = payload.split("_", 1)[1]
        loaded = load_game(gid)
        
        if not loaded:
            bot.send_message(message.chat.id, "⛔ بازی مورد نظر پیدا نشد یا منقضی شده‌است.")
            return
        
        chat_id, message_id, state, _ = loaded
        
        if state.get("finished"):
            bot.send_message(message.chat.id, "⛔ این بازی قبلاً به پایان رسیده‌است.")
            return
        
        if who_is_player(state, user.id):
            header, markup = render_board(state)
            bot.send_message(message.chat.id, "✅ شما در حال حاضر در این بازی شرکت دارید:", reply_markup=markup)
            return
        
        if state["game_type"] != "pvp":
            bot.send_message(message.chat.id, "⛔ این بازی مخصوص دو نفر (PVP) نیست.")
            return
        
        if state["players"].get("O") is None and user.id != state["players"].get("X"):
            state["players"]["O"] = user.id
            save_game(gid, chat_id, message_id, state)
            header, markup = render_board(state)
            try:
                if message_id:
                    bot.edit_message_text(
                        f"✅ {safe_get_username(user.id)} با موفقیت به بازی پیوست!",
                        chat_id,
                        message_id
                    )
                    bot.edit_message_text(header, chat_id, message_id, reply_markup=markup)
                    # پیام بورد برای بازیکن دوم
                    msg2 = bot.send_message(message.chat.id, "بورد بازی:", reply_markup=markup)
                    save_player_message(state, "O", message.chat.id, msg2.message_id, gid)
                else:
                    msg2 = bot.send_message(chat_id, f"✅ {safe_get_username(user.id)} به بازی پیوست")
                    save_player_message(state, "O", chat_id, msg2.message_id, gid)
                bot.send_message(
                    message.chat.id,
                    f"✅ شما با موفقیت به بازی پیوستید!\n"
                    f"🔷 بازیکن X: {safe_get_username(state['players'].get('X'))}\n"
                    f"🔶 بازیکن O: شما\n\n"
                    f"لطفا منتظر نوبت خود باشید...",
                    reply_markup=markup
                )
            except Exception as e:
                print(f"Join error: {e}")
            return
        else:
            bot.send_message(message.chat.id, "⛔ این بازی پر شده‌است یا شما سازنده بازی هستید.")
            return

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🎮 شروع بازی جدید", callback_data="menu_play"))
    markup.add(types.InlineKeyboardButton("📖 راهنمای بازی", callback_data="menu_help"))
    markup.add(types.InlineKeyboardButton("🏆 آمار من", callback_data="menu_stats"))
    
    text = (
        f"👋 سلام {user.first_name}!\n"
        "به بات بازی دوز خوش آمدید!\n\n"
        "میتوانید با دوستان خود بازی کنید یا مقابل هوش مصنوعی مسابقه دهید."
    )
    bot.send_message(message.chat.id, text, reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith("menu_"))
def handle_menu(call: types.CallbackQuery):
    cmd = call.data.split("_", 1)[1]
    
    if cmd == "play":
        markup = types.InlineKeyboardMarkup()
        gid = generate_game_id()
        state = new_game(game_type="pvp", creator_id=call.from_user.id)
        state["_id"] = gid
        save_game(gid, call.message.chat.id, call.message.message_id, state)
        
        markup.add(types.InlineKeyboardButton("👥 بازی دو نفره (PVP)", callback_data=f"mode_pvp|{gid}"))
        markup.add(types.InlineKeyboardButton("🤖 بازی با کامپیوتر (AI)", callback_data=f"mode_ai|{gid}"))
        
        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            "لطفا حالت بازی را انتخاب کنید:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=markup
        )
    
    elif cmd == "help":
        help_text = (
            "📖 راهنمای بازی دوز:\n\n"
            "🔸 هر بازیکن به نوبت در یکی از خانه‌های خالی علامت می‌گذارد\n"
            "🔸 بازیکن X همیشه شروع‌کننده بازی است\n"
            "🔸 برنده کسی است که اولین بار سه علامت خود را در یک ردیف قرار دهد\n\n"
            "💡 دستورات:\n"
            "/start - نمایش منوی اصلی\n"
            "/play - شروع بازی جدید\n"
            "/stats - نمایش آمار بازی\n\n"
            "🎮 برای شروع بازی جدید از منوی اصلی گزینه 'شروع بازی جدید' را انتخاب کنید"
        )
        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            help_text,
            call.message.chat.id,
            call.message.message_id
        )
    
    elif cmd == "stats":
        user_id = call.from_user.id
        stats = get_or_create_stats(user_id)
        stats_text = (
            f"📊 آمار بازی‌های شما:\n\n"
            f"✅ بردها: {stats['wins']}\n"
            f"❌ باخت‌ها: {stats['losses']}\n"
            f"🤝 تساوی‌ها: {stats['draws']}\n"
            f"🔥 رکورد برد متوالی: {stats['best_streak']}\n"
            f"🏆 بردهای متوالی فعلی: {stats['win_streak']}"
        )
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            stats_text
        )


@bot.callback_query_handler(func=lambda call: call.data.startswith("mode_"))
def handle_mode(call: types.CallbackQuery):
    try:
        parts = call.data.split("|")
        mode = parts[0].split("_", 1)[1]
        gid = parts[1]
        loaded = load_game(gid)
        if not loaded:
            bot.answer_callback_query(call.id, "بازی پیدا نشد یا منقضی شده.", show_alert=True)
            return
        chat_id, message_id, state, _ = loaded
        state["_id"] = gid
        state["players"]["X"] = call.from_user.id
        
        if mode == "pvp":
            state["game_type"] = "pvp"
            save_game(gid, call.message.chat.id, call.message.message_id, state)
            
            try:
                me = bot.get_me()
                start_payload = f"join_{gid}"
                link = f"https://t.me/{me.username}?start={start_payload}"
                
                # Improved invite message
                kb = types.InlineKeyboardMarkup()
                kb.add(types.InlineKeyboardButton("📩 ارسال لینک دعوت", url=f"tg://share?url={link}"))
                
                invite_msg = (
                    f"🔗 لینک دعوت برای بازی دو نفره:\n\n"
                    f"{link}\n\n"
                    f"این لینک را برای دوست خود ارسال کنید تا به بازی بپیوندد."
                )
                bot.send_message(call.message.chat.id, invite_msg, reply_markup=kb)
            except Exception as e:
                print(f"Invite link error: {e}")
                bot.send_message(call.message.chat.id, "بازی PvP ایجاد شد! لینک دعوت دوست خود را ارسال کنید.")
            
            # Also update the creating message
            header, markup = render_board(state)
            bot.edit_message_text(
                "بازی دو نفره ایجاد شد! منتظر بازیکن دوم هستیم...",
                call.message.chat.id,
                call.message.message_id
            )
            bot.edit_message_text(
                header,
                call.message.chat.id,
                call.message.message_id,
                reply_markup=markup
            )
            bot.answer_callback_query(call.id)
        else:
            state["game_type"] = "ai"
            state["players"]["O"] = None
            save_game(gid, call.message.chat.id, call.message.message_id, state)
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("🔰 آسان", callback_data=f"diff_easy|{gid}"))
            kb.add(types.InlineKeyboardButton("⚙️ متوسط", callback_data=f"diff_medium|{gid}"))
            kb.add(types.InlineKeyboardButton("🔥 سخت", callback_data=f"diff_hard|{gid}"))
            bot.edit_message_text("سطح هوش مصنوعی را انتخاب کنید:", call.message.chat.id, call.message.message_id, reply_markup=kb)
            bot.answer_callback_query(call.id)
    except Exception as e:
        bot.answer_callback_query(call.id, "خطا در انتخاب حالت.")
        print(f"handle_mode error: {e}")


@bot.callback_query_handler(func=lambda call: call.data.startswith("diff_"))
def handle_diff(call: types.CallbackQuery):
    try:
        parts = call.data.split("|")
        diff = parts[0].split("_", 1)[1]
        gid = parts[1]
        loaded = load_game(gid)
        if not loaded:
            bot.answer_callback_query(call.id, "بازی پیدا نشد.")
            return
        chat_id, message_id, state, _ = loaded
        state["_id"] = gid
        state["ai_difficulty"] = diff
        state["players"]["O"] = "AI"
        save_game(gid, call.message.chat.id, call.message.message_id, state)
        header, kb = render_board(state)
        
        try:
            msg = bot.edit_message_text(header, call.message.chat.id, call.message.message_id, reply_markup=kb)
            save_game(gid, call.message.chat.id, msg.message_id, state)
        except Exception:
            bot.send_message(call.message.chat.id, header, reply_markup=kb)
        
        
        if state["game_type"] == "ai" and state["current_player"] == "O":
            threading.Thread(target=do_ai_move, args=(gid,)).start()
        
        bot.answer_callback_query(call.id, f"سطح AI: {diff}")
    except Exception as e:
        bot.answer_callback_query(call.id, "خطا در انتخاب سختی.")
        print(f"handle_diff error: {e}")


@bot.message_handler(commands=["play"])
def cmd_play(message: types.Message):
    gid = generate_game_id()
    state = new_game("pvp", message.from_user.id)
    state["_id"] = gid
    save_game(gid, message.chat.id, None, state)
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("👥 بازی دو نفره (PVP)", callback_data=f"mode_pvp|{gid}"))
    kb.add(types.InlineKeyboardButton("🤖 بازی با کامپیوتر (AI)", callback_data=f"mode_ai|{gid}"))
    bot.send_message(message.chat.id, "لطفا حالت بازی را انتخاب کنید:", reply_markup=kb)


@bot.callback_query_handler(func=lambda call: call.data.startswith("move_"))
def handle_move(call: types.CallbackQuery):
    try:
        payload = call.data.split("_", 1)[1]
        gid, pos = payload.split("|")
        pos = int(pos)
        loaded = load_game(gid)
        if not loaded:
            bot.answer_callback_query(call.id, "این بازی پیدا نشد یا منقضی شده.", show_alert=True)
            return
        chat_id, message_id, state, _ = loaded
        state["_id"] = gid
        if state.get("finished"):
            bot.answer_callback_query(call.id, "بازی قبلاً تمام شده.", show_alert=True)
            return

        lock = get_game_lock(gid)
        acquired = lock.acquire(blocking=False)
        if not acquired:
            bot.answer_callback_query(call.id, "در حال پردازش حرکت قبلی...", show_alert=False)
            return

        try:
            user = call.from_user
            player = who_is_player(state, user.id)
            
            if not player:
                if state["game_type"] == "pvp" and state["players"].get("O") is None and user.id != state["players"].get("X"):
                    state["players"]["O"] = user.id
                    player = "O"
                    save_game(gid, chat_id, message_id, state)
                    
                    try:
                        header, kb = render_board(state)
                        if message_id:
                            bot.edit_message_text(f"✅ {user.first_name} به بازی پیوست!", chat_id, message_id)
                            bot.edit_message_text(header, chat_id, message_id, reply_markup=kb)
                    except Exception:
                        pass
                else:
                    bot.answer_callback_query(call.id, "شما در این بازی نیستید یا بازی پر است.", show_alert=True)
                    return

            if player != state["current_player"]:
                bot.answer_callback_query(call.id, "الان نوبت شما نیست.", show_alert=True)
                return

            if state["board"][pos] != "":
                bot.answer_callback_query(call.id, "این خانه قبلاً انتخاب شده.", show_alert=True)
                return

            state["board"][pos] = player
            state["history"].append({"player": player, "pos": pos, "time": int(time.time())})
            update_last_activity(gid)
            save_game(gid, chat_id, message_id, state)

            winner_line = check_winner(state["board"])
            if winner_line:
                win_player, line = winner_line
                finish_game_and_announce(gid, win_player, highlight=line)
                bot.answer_callback_query(call.id, "بازی تمام شد.")
                return

            if is_draw(state["board"]):
                finish_game_and_announce(gid, "draw")
                bot.answer_callback_query(call.id, "مساوی شد.")
                return

            state["current_player"] = "O" if state["current_player"] == "X" else "X"
            save_game(gid, chat_id, message_id, state)
            header, kb = render_board(state)
            try:
               
                for p in ["X", "O"]:
                    user_id = state["players"].get(p)
                    if not user_id:
                        continue
                    msginfo = state.get("messages", {}).get(p)
                    updated = False
                    if msginfo and msginfo.get("chat_id") and msginfo.get("message_id"):
                        try:
                            bot.edit_message_text(header + "\n\n⏳ حرکت ثبت شد.", msginfo["chat_id"], msginfo["message_id"], reply_markup=kb)
                            updated = True
                        except Exception:
                            pass
                    if not updated:
                        
                        try:
                            msg = bot.send_message(user_id, header + "\n\n⏳ حرکت ثبت شد.", reply_markup=kb)
                            if "messages" not in state:
                                state["messages"] = {}
                            state["messages"][p] = {"chat_id": user_id, "message_id": msg.message_id}
                            save_game(gid, chat_id, message_id, state)
                        except Exception:
                            pass
                
                if message_id:
                    bot.edit_message_text(header + "\n\n⏳ حرکت ثبت شد.", chat_id, message_id, reply_markup=kb)
                else:
                    bot.send_message(chat_id, header)
            except Exception:
                pass
            
            bot.answer_callback_query(call.id, "حرکت ثبت شد.")

            if state["game_type"] == "ai" and state["players"].get("O") == "AI" and state["current_player"] == "O":
                threading.Thread(target=do_ai_move, args=(gid,)).start()

        finally:
            lock.release()

    except Exception as e:
        bot.answer_callback_query(call.id, "خطا در پردازش حرکت.")
        print(f"handle_move error: {e}")


def do_ai_move(gid: str):
    time.sleep(1)
    loaded = load_game(gid)
    if not loaded:
        return
    chat_id, message_id, state, _ = loaded
    if state.get("finished"):
        return
    lock = get_game_lock(gid)
    with lock:
        move = ai_choose_move(state)
        if move is None:
            return
        
        state["board"][move] = "O"
        state["history"].append({"player": "O", "pos": move, "time": int(time.time())})
        update_last_activity(gid)
        save_game(gid, chat_id, message_id, state)

        winner_line = check_winner(state["board"])
        if winner_line:
            win_player, line = winner_line
            finish_game_and_announce(gid, win_player, highlight=line)
            return
        
        if is_draw(state["board"]):
            finish_game_and_announce(gid, "draw")
            return

        state["current_player"] = "X"
        save_game(gid, chat_id, message_id, state)
        header, kb = render_board(state)
        try:
            if message_id:
                bot.edit_message_text(header, chat_id, message_id, reply_markup=kb)
            else:
                bot.send_message(chat_id, header)
        except Exception:
            pass



def save_player_message(state, player, chat_id, message_id, gid):
    if "messages" not in state:
        state["messages"] = {}
    state["messages"][player] = {"chat_id": chat_id, "message_id": message_id}
    
    if player == "X":
        save_game(gid, chat_id, message_id, state)
    else:
        
        save_game(gid, state["messages"].get("X", {}).get("chat_id", chat_id), state["messages"].get("X", {}).get("message_id", message_id), state)



init_db()
threading.Thread(target=inactivity_watcher, daemon=True).start()

if __name__ == "__main__":
    print("Bot started with improved UI/UX and fixed bugs...")
    bot.infinity_polling(timeout=60, long_polling_timeout=60)