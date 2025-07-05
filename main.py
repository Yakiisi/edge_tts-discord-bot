import os
import sys
import json
import re
import tempfile
import asyncio
import time
import threading
import psutil

import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv
import edge_tts

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

# ── 環境変数の読み込み ──
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

# ── 定数 ──
GLOBAL_DICT_FILE = "global_dict.json"
USER_SETTINGS_FILE = "user_settings.json"
SERVER_DICTS_DIR = "server_dicts"  # サーバーごとの辞書を保存するディレクトリ
# SERVER_SETTINGS_DIR はユーザー設定に切り替えるため不要になりますが、既存ファイルの削除ロジックのために残します。
SERVER_SETTINGS_DIR = "server_settings" 
INVITE_URL = ("https://discord.com/oauth2/authorize?client_id=1364493244343255111&permissions=2150976512&integration_type=0&scope=bot+applications.commands") #自分のclient_idに書き換えてください。
TEMP_AUDIO_DIR = "temp_audio"

# 一時ディレクトリの作成
os.makedirs(TEMP_AUDIO_DIR, exist_ok=True)

# ── Bot初期化 ──
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.voice_states = True

bot = commands.Bot(command_prefix="e!", intents=intents, help_command=None)

# 音声再生キュー
voice_queues: dict[int, asyncio.Queue[bytes]] = {}
voice_clients: dict[int, discord.VoiceClient] = {}
reading_channels: dict[int, int] = {}

# 最終アクティブ時刻を記録 (自動退出用)
last_active_time: dict[int, float] = {}

# ユーザーごとの読み上げ速度設定 (tts_voiceもここに追加)
user_settings: dict[str, dict] = {} # { "user_id": {"tts_speed": 0.0, "tts_voice": "ja-JP-NanamiNeural"}, ... }

def load_user_settings():
    """ユーザーごとの設定をファイルから読み込みます。"""
    if os.path.exists(USER_SETTINGS_FILE):
        try:
            with open(USER_SETTINGS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"警告: {USER_SETTINGS_FILE} が壊れています。空の辞書を読み込みます。")
            return {}
    return {}

def save_user_settings():
    """ユーザーごとの設定をファイルに保存します。"""
    try:
        with open(USER_SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(user_settings, f, indent=4, ensure_ascii=False)
    except IOError as e:
        print(f"エラー: ユーザー設定 {USER_SETTINGS_FILE} の保存に失敗しました: {e}")

# get_user_speed に加えて、get_user_voice 関数を追加
def get_user_speed(user_id: int) -> float:
    """指定されたユーザーIDの読み上げ速度を取得します。デフォルトは0.0。"""
    user_id_str = str(user_id)
    return user_settings.get(user_id_str, {}).get("tts_speed", 0.0)

def set_user_speed(user_id: int, speed: float):
    """指定されたユーザーIDの読み上げ速度を設定します。"""
    user_id_str = str(user_id)
    if user_id_str not in user_settings:
        user_settings[user_id_str] = {}
    user_settings[user_id_str]["tts_speed"] = speed
    save_user_settings()

def get_user_voice(user_id: int) -> str:
    """指定されたユーザーIDの読み上げ声を取得します。デフォルトはNanami。"""
    user_id_str = str(user_id)
    # デフォルト値を "ja-JP-NanamiNeural" に設定
    return user_settings.get(user_id_str, {}).get("tts_voice", "ja-JP-NanamiNeural")

def set_user_voice(user_id: int, voice_name: str):
    """指定されたユーザーIDの読み上げ声を設定します。"""
    user_id_str = str(user_id)
    if user_id_str not in user_settings:
        user_settings[user_id_str] = {}
    user_settings[user_id_str]["tts_voice"] = voice_name
    save_user_settings()

# 起動時にユーザー設定を読み込む
user_settings = load_user_settings()

# ── グローバル辞書関数 ──
def load_global_dictionary():
    """グローバル辞書をファイルから読み込みます。"""
    if os.path.exists(GLOBAL_DICT_FILE):
        try:
            with open(GLOBAL_DICT_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"警告: {GLOBAL_DICT_FILE} が壊れています。空の辞書を読み込みます。")
            return {}
    return {}

def save_global_dictionary(data):
    """グローバル辞書をファイルに保存します。"""
    try:
        with open(GLOBAL_DICT_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except IOError as e:
        print(f"エラー: グローバル辞書の保存に失敗しました: {e}")

global_dict = load_global_dictionary()

# ── サーバーごとの辞書関数 ──
def get_server_dict_path(guild_id: int):
    """指定されたギルドIDのサーバー辞書ファイルのパスを返します。"""
    return os.path.join(SERVER_DICTS_DIR, f"{guild_id}.json")

def load_server_dictionary(guild_id: int):
    """指定されたギルドIDのサーバー辞書をファイルから読み込みます。"""
    path = get_server_dict_path(guild_id)
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"警告: サーバー辞書 {path} が壊れています。空の辞書を読み込みます。")
            return {}
    return {}

def save_server_dictionary(guild_id: int, data: dict):
    """指定されたギルドIDのサーバー辞書をファイルに保存します。"""
    path = get_server_dict_path(guild_id)
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except IOError as e:
        print(f"エラー: サーバー辞書 {path} の保存に失敗しました: {e}")

def apply_dictionary(text: str, guild_id: int) -> str:
    """テキストにサーバー固有辞書とグローバル辞書を適用します。"""
    # サーバー固有の辞書を先に適用
    server_dict = load_server_dictionary(guild_id)
    for original, replacement in server_dict.items():
        text = text.replace(original, replacement)
    
    # その後、グローバル辞書を適用
    for original, replacement in global_dict.items():
        text = text.replace(original, replacement)
    return text

# ── 音声合成関連関数 ──
async def generate_tts(text: str, user_id: int, guild_id: int) -> bytes: 
    """テキストからTTS音声を生成します。"""
    # voice をユーザー設定から取得
    voice = get_user_voice(user_id) # 変更点
    
    tts_speed_float = get_user_speed(user_id)

    communicate_kwargs = {"text": text, "voice": voice}

    if tts_speed_float != 0.0:
        if tts_speed_float > 0:
            rate_str = f"+{int(tts_speed_float)}%"
        else:
            rate_str = f"{int(tts_speed_float)}%"
        communicate_kwargs["rate"] = rate_str
    
    communicate = edge_tts.Communicate(**communicate_kwargs)
    
    with tempfile.NamedTemporaryFile(suffix=".mp3", dir=TEMP_AUDIO_DIR, delete=False) as f:
        filepath = f.name
    await communicate.save(filepath)
    
    with open(filepath, 'rb') as f:
        audio_data = f.read()
    os.remove(filepath)

    return audio_data

# ── 音声再生関連関数 ──
def play_audio(guild_id: int, audio_data: bytes):
    """指定されたギルドのVCで音声データを再生キューに追加します。"""
    vc = voice_clients.get(guild_id)
    if not vc or not vc.is_connected():
        return

    with tempfile.NamedTemporaryFile(suffix=".mp3", dir=TEMP_AUDIO_DIR, delete=False) as f:
        f.write(audio_data)
        filepath = f.name

    async def play_next(error):
        if error:
            print(f"再生エラー: {error}")
        
        if os.path.exists(filepath):
            os.remove(filepath)
        
        if not voice_queues[guild_id].empty():
            next_audio = await voice_queues[guild_id].get()
            play_audio(guild_id, next_audio)

    async def add_and_play():
        await voice_queues[guild_id].put(audio_data)
        if not vc.is_playing() and voice_queues[guild_id].qsize() == 1:
            first_audio = await voice_queues[guild_id].get()
            with tempfile.NamedTemporaryFile(suffix=".mp3", dir=TEMP_AUDIO_DIR, delete=False) as f:
                f.write(first_audio)
                first_filepath = f.name
            vc.play(discord.FFmpegPCMAudio(first_filepath), after=lambda e: bot.loop.create_task(play_next(e)))

    bot.loop.create_task(add_and_play())

# ── Utility関数 ──
def create_progress_bar(percentage):
    """進捗バーの文字列を生成します。"""
    bar_length = 20
    filled_length = int(bar_length * percentage / 100)
    bar = '█' * filled_length + '　' * (bar_length - filled_length)
    return f"{percentage:.1f}% [{bar}]"

# ── Discord Bot イベントハンドラ ──
@bot.event
async def on_ready():
    """BotがDiscordに接続した際に実行されます。"""
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')
    await bot.tree.sync()
    print('スラッシュコマンド同期完了')

    if not check_idle_voice_channels.is_running():
        check_idle_voice_channels.start()
        print("アイドルVCチェックタスクを開始しました。")
    
    if hasattr(bot, 'gui_app'):
        bot.gui_app.update_dashboard_display()
        bot.gui_app.log_output.insert(tk.END, f"Bot: {bot.user.name}としてログインしました。\n")
        bot.gui_app.log_output.see(tk.END)

    await bot.change_presence(status=discord.Status.online, activity=discord.Game('e!help | /help'))

@bot.event
async def on_guild_join(guild):
    """Botが新しいサーバーに参加した際に実行されます。"""
    print(f"Botが新しいサーバーに参加しました: {guild.name} (ID: {guild.id})")
    if hasattr(bot, 'gui_app'):
        bot.gui_app.update_dashboard_display()
        bot.gui_app.log_output.insert(tk.END, f"Bot: 新しいサーバーに参加: {guild.name}\n")
        bot.gui_app.log_output.see(tk.END)

@bot.event
async def on_guild_remove(guild):
    """Botがサーバーを退出した際に実行されます。"""
    print(f"Botがサーバーを退出しました: {guild.name} (ID: {guild.id})")
    if guild.id in voice_clients:
        await voice_clients[guild.id].disconnect()
        del voice_clients[guild.id]
    if guild.id in reading_channels:
        del reading_channels[guild.id]
    if guild.id in voice_queues:
        del voice_queues[guild.id]
    if guild.id in last_active_time:
        del last_active_time[guild.id]
    
    # サーバー辞書ファイルを削除
    server_dict_path = get_server_dict_path(guild.id)
    if os.path.exists(server_dict_path):
        os.remove(server_dict_path)
        print(f"サーバー辞書ファイルを削除しました: {server_dict_path}")

    if hasattr(bot, 'gui_app'):
        bot.gui_app.update_dashboard_display()
        bot.gui_app.log_output.insert(tk.END, f"Bot: サーバーを退出: {guild.name}\n")
        bot.gui_app.log_output.see(tk.END)

@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    """ボイスチャンネルの状態が更新された際に実行されます (入室/退出のアナウンス)。"""
    if member.bot:
        return
    gid = member.guild.id
    if gid not in voice_clients:
        return
    vc = voice_clients[gid]

    # 重要: Botが接続しているVCにユーザーの動きがあった場合にアクティブタイムを更新
    # これにより、ユーザーがいる限りBotが自動退出しないようにする
    if vc.channel.id == (before.channel.id if before.channel else -1) or \
       vc.channel.id == (after.channel.id if after.channel else -1):
        last_active_time[gid] = asyncio.get_event_loop().time()


    # 入室
    if after.channel and (before.channel is None or before.channel != after.channel):
        if vc.channel.id == after.channel.id:
            announce = f"{member.display_name} さんが接続しました。"
            tts = await generate_tts(announce, member.id, gid) 
            play_audio(gid, tts)

    # 退出
    if before.channel and after.channel is None:
        if vc.channel.id == before.channel.id:
            announce = f"{member.display_name} さんが退出しました。"
            tts = await generate_tts(announce, member.id, gid)
            play_audio(gid, tts)

# ── 自動退出タスク ──
@tasks.loop(minutes=1)
async def check_idle_voice_channels():
    """アイドル状態のボイスチャンネルからBotを自動退出させます。"""
    current_time = asyncio.get_event_loop().time()
    guild_ids_to_disconnect = []

    for gid, vc in list(voice_clients.items()):
        # VCに接続しており、かつBot以外のメンバーが0人の場合
        if vc and vc.channel and len([m for m in vc.channel.members if not m.bot]) == 0:
            # 最後にアクティブだった時刻から1s以上経過しているかチェック
            if gid in last_active_time and (current_time - last_active_time[gid] > 1): 
                guild_ids_to_disconnect.append(gid)
        else:
            # VCに誰かいる、またはVCに接続していない場合は、アクティブ時刻を更新
            # （on_voice_state_updateでも更新されているが、念のためこちらでも行う）
            last_active_time[gid] = current_time

    for gid in guild_ids_to_disconnect:
        vc = voice_clients.get(gid)
        if vc:
            await vc.disconnect()
            print(f"アイドル状態のためVCから退出しました: {bot.get_guild(gid).name}")
            # 関連する辞書から情報を削除
            if gid in voice_clients: del voice_clients[gid]
            if gid in voice_queues: del voice_queues[gid]
            if gid in reading_channels: del reading_channels[gid]
            if gid in last_active_time: del last_active_time[gid]
            if hasattr(bot, 'gui_app'):
                bot.gui_app.log_output.insert(tk.END, f"Bot: アイドル状態のためVCから退出: {bot.get_guild(gid).name}\n")
                bot.gui_app.log_output.see(tk.END)

@bot.hybrid_command(name="invite", description="Botの招待リンクを表示します。")
async def invite(ctx: commands.Context):
    embed=discord.Embed(title="招待する", url=INVITE_URL, color=0x48d282)
    embed.set_author(name="Botの招待リンクです", url=INVITE_URL)
    await ctx.reply(embed=embed)

@bot.hybrid_command(name="help", description="Botのコマンド一覧と説明を表示します。")
async def help(ctx: commands.Context):
    """Botのコマンド一覧と説明を表示します。"""
    embed = discord.Embed(
        title="Botコマンドヘルプ",
        description="利用可能なコマンドの一覧です。",
        color=0x3498DB # 青色の良い色
    )

    # 各コマンドの説明を追加
    # descriptionはスラッシュコマンドの説明文にもなります。
    embed.add_field(name="`/join [チャンネル名]`", value="Botをボイスチャンネルに参加させます。チャンネル名を指定しない場合、コマンド実行者のVCに参加します。", inline=False)
    embed.add_field(name="`/leave`", value="Botをボイスチャンネルから退出させます。", inline=False)
    embed.add_field(name="`/set_reading_channel [チャンネル名]`", value="メッセージを読み上げるテキストチャンネルを設定します。チャンネル名を指定しない場合、コマンド実行チャンネルが設定されます。", inline=False)
    embed.add_field(name="`/add_word <元の語句> <読み>`", value="サーバー専用辞書に単語を追加します。（例: `/add_word hello こんにちは`）", inline=False)
    embed.add_field(name="`/remove_word <元の語句>`", value="サーバー専用辞書から単語を削除します。", inline=False)
    embed.add_field(name="`/list_words`", value="サーバー専用辞書に登録されている単語一覧を表示します。", inline=False)
    embed.add_field(name="`/help`", value="このヘルプメッセージを表示します。", inline=False)
    embed.add_field(name="`/status`", value="Botの動作状況を表示します。", inline=False)
    embed.add_field(name="`/invite`", value="Botの招待リンクを表示します。", inline=False)

    embed.set_footer(text=f"{bot.user.name} | コマンドプレフィックス: e!")
    
    await ctx.reply(embed=embed) # ephemeral=True で、コマンド実行者のみに見えるようにする

    if hasattr(bot, 'gui_app'):
        bot.gui_app.log_output.insert(tk.END, f"Bot: サーバー「{ctx.guild.name}」で /help コマンドが実行されました。\n")
        bot.gui_app.log_output.see(tk.END)

# ── Discord コマンド ──
@bot.hybrid_command(name="join", description="ボイスチャンネルに参加します。", aliases=["vjoin"])
async def join(ctx: commands.Context, channel: discord.VoiceChannel = None):
    """Botをボイスチャンネルに接続させます。"""
    if not ctx.guild:
        embed = discord.Embed(title="エラー", description="このコマンドはサーバーでのみ使用できます。", color=0xFF0000)
        await ctx.reply(embed=embed, ephemeral=True)
        return

    if not ctx.author.voice:
        # Embed for error
        embed = discord.Embed(
            title="エラー",
            description="ボイスチャンネルに接続してからコマンドを実行してください。",
            color=0xFF0000 # 赤色
        )
        await ctx.reply(embed=embed, ephemeral=True)
        return

    if channel is None:
        channel = ctx.author.voice.channel

    # ここから権限チェックを追加
    permissions = channel.permissions_for(ctx.guild.me)
    if not permissions.connect:
        embed = discord.Embed(
            title="権限エラー",
            description=f"**{channel.name}** に接続する権限がありません。\n"
                        "ボットに「接続」権限を与えてください。",
            color=0xFF0000
        )
        await ctx.reply(embed=embed, ephemeral=True)
        return
    if not permissions.speak:
        embed = discord.Embed(
            title="権限エラー",
            description=f"**{channel.name}** で発言する権限がありません。\n"
                        "ボットに「発言」権限を与えてください。",
            color=0xFF0000
        )
        await ctx.reply(embed=embed, ephemeral=True)
        return
    # 権限チェックここまで

    if ctx.guild.id in voice_clients and voice_clients[ctx.guild.id].is_connected():
        if voice_clients[ctx.guild.id].channel.id == channel.id:
            # Embed for already connected
            embed = discord.Embed(
                title="既に接続済み",
                description=f"既に **{channel.name}** に接続しています。",
                color=0xFFA500 # オレンジ色
            )
            await ctx.reply(embed=embed, ephemeral=True)
            return
        else:
            old_channel_name = voice_clients[ctx.guild.id].channel.name if voice_clients[ctx.guild.id].channel else "不明なチャンネル"
            await voice_clients[ctx.guild.id].move_to(channel)
            
            # Embed for channel moved
            embed = discord.Embed(
                title="ボイスチャンネル移動",
                description=f"ボイスチャンネルを **{old_channel_name}** から **{channel.name}** に移動しました。",
                color=0x00BFFF # ディープスカイブルー
            )
            embed.add_field(name="読み上げチャンネル", value=ctx.channel.mention, inline=False)
            embed.set_footer(text=f"コマンド実行者: {ctx.author.display_name}", icon_url=ctx.author.avatar.url if ctx.author.avatar else None)
            await ctx.reply(embed=embed, ephemeral=False)

            reading_channels[ctx.guild.id] = ctx.channel.id
            last_active_time[ctx.guild.id] = asyncio.get_event_loop().time()
            if hasattr(bot, 'gui_app'): bot.gui_app.update_dashboard_display()
            
            # VC移動時も読み上げ
            vc_announce = f"ボイスチャンネルを {channel.name} に移動しました。"
            tts_vc_announce = await generate_tts(vc_announce, ctx.author.id, ctx.guild.id) 
            play_audio(ctx.guild.id, tts_vc_announce)
            return

    vc = await channel.connect()
    voice_clients[ctx.guild.id] = vc
    voice_queues[ctx.guild.id] = asyncio.Queue()
    reading_channels[ctx.guild.id] = ctx.channel.id
    last_active_time[ctx.guild.id] = asyncio.get_event_loop().time()
    
    # Embed for successful connection
    embed = discord.Embed(
        title="接続成功",
        description=f"**{channel.name}** に接続しました。",
        color=0x00FF00 # 緑色
    )
    embed.add_field(name="読み上げチャンネル", value=ctx.channel.mention, inline=False)
    embed.set_footer(text=f"コマンド実行者: {ctx.author.display_name}", icon_url=ctx.author.avatar.url if ctx.author.avatar else None)
    await ctx.reply(embed=embed, ephemeral=False)

    if hasattr(bot, 'gui_app'): bot.gui_app.update_dashboard_display()

    # BotがVCに接続した際に読み上げる
    connect_message = f"接続しました。"
    tts_connect_message = await generate_tts(connect_message, ctx.author.id, ctx.guild.id)
    play_audio(ctx.guild.id, tts_connect_message)


@bot.hybrid_command(name="leave", description="ボイスチャンネルから切断します。", aliases=["bye"])
async def leave(ctx: commands.Context):
    """Botをボイスチャンネルから切断させます。"""
    if not ctx.guild:
            embed = discord.Embed(title="エラー", description="このコマンドはサーバーでのみ使用できます。", color=0xFF0000)
            await ctx.reply(embed=embed, ephemeral=True)
            return

    if ctx.guild.id in voice_clients and voice_clients[ctx.guild.id].is_connected():
        current_channel_name = voice_clients[ctx.guild.id].channel.name if voice_clients[ctx.guild.id].channel else "現在のチャンネル"
        await voice_clients[ctx.guild.id].disconnect()
        
        # Embed for successful disconnection
        embed = discord.Embed(
            title="切断しました",
            description=f"**{current_channel_name}** から切断しました。",
            color=0x800080 # 紫色
        )
        embed.set_footer(text=f"コマンド実行者: {ctx.author.display_name}", icon_url=ctx.author.avatar.url if ctx.author.avatar else None)
        await ctx.reply(embed=embed, ephemeral=False)

        del voice_clients[ctx.guild.id]
        if ctx.guild.id in voice_queues:
            del voice_queues[ctx.guild.id]
        if ctx.guild.id in reading_channels:
            del reading_channels[ctx.guild.id]
        if ctx.guild.id in last_active_time:
            del last_active_time[ctx.guild.id]
        if hasattr(bot, 'gui_app'): bot.gui_app.update_dashboard_display()
    else:
        # Embed for not connected
        embed = discord.Embed(
            title="エラー",
            description="ボイスチャンネルに接続していません。",
            color=0xFF0000 # 赤色
        )
        await ctx.reply(embed=embed, ephemeral=True)

# ── Discord コマンド ── (既存のコマンドに追加)

# setvoice コマンドの追加
@bot.hybrid_command(name="setvoice", description="読み上げの声を変更します。", aliases=["voice"])
@app_commands.describe(voice_name="使用したい声の名前を選択してください (Nanami, Keita)")
@app_commands.choices(
    voice_name=[
        app_commands.Choice(name="Nanami (女性)", value="ja-JP-NanamiNeural"),
        app_commands.Choice(name="Keita (男性)", value="ja-JP-KeitaNeural"),
    ]
)
async def setvoice(ctx: commands.Context, voice_name: str):
    """ユーザーごとの読み上げの声を変更します。"""
    # 選択肢はアプリコマンドで定義されているため、ここではバリデーション不要
    # ただし、将来的に手動で入力できるようにする場合や、Choice以外の値をチェックする場合はここに追加
    
    set_user_voice(ctx.author.id, voice_name)
    
    # 選択された声の表示名を整形
    display_voice_name = ""
    if voice_name == "ja-JP-NanamiNeural":
        display_voice_name = "Nanami (女性)"
    elif voice_name == "ja-JP-KeitaNeural":
        display_voice_name = "Keita (男性)"
    else:
        display_voice_name = voice_name # 万が一不明な値が来た場合

    embed = discord.Embed(
        title="読み上げの声を変更しました",
        description=f"あなたの読み上げの声: **{display_voice_name}**",
        color=0x9966CC # 紫系
    )
    embed.set_footer(text=f"設定者: {ctx.author.display_name}", icon_url=ctx.author.avatar.url if ctx.author.avatar else None)
    await ctx.reply(embed=embed, ephemeral=False)

    if hasattr(bot, 'gui_app'):
        bot.gui_app.log_output.insert(tk.END, f"Bot: ユーザー({ctx.author.display_name})の読み上げの声を{display_voice_name}に設定しました。\n")
        bot.gui_app.log_output.see(tk.END)

@bot.hybrid_command(name="set_reading_channel", description="メッセージを読み上げるテキストチャンネルを設定します。")
@app_commands.describe(channel="読み上げチャンネルに設定するテキストチャンネル (指定しない場合、コマンドを実行したチャンネル)")
async def set_reading_channel(ctx: commands.Context, channel: discord.TextChannel = None):
    """指定されたテキストチャンネルを読み上げチャンネルに設定します。(引数を指定しない場合、コマンドを実行したチャンネル)"""
    if ctx.guild.id not in voice_clients or not voice_clients[ctx.guild.id].is_connected():
        embed = discord.Embed(
            title="エラー",
            description="ボイスチャンネルに接続してから実行してください。",
            color=0xFF0000
        )
        await ctx.reply(embed=embed, ephemeral=True)
        return

    if channel is None:
        channel = ctx.channel
        if not isinstance(channel, discord.TextChannel):
            embed = discord.Embed(
                title="エラー",
                description="このコマンドはテキストチャンネルで実行してください、または有効なテキストチャンネルを指定してください。",
                color=0xFF0000
            )
            await ctx.reply(embed=embed, ephemeral=True)
            return

    # ここからチャンネル閲覧権限のチェックを追加
    permissions = channel.permissions_for(ctx.guild.me)
    if not permissions.read_messages: # メッセージを読む権限 (View Channel)
        embed = discord.Embed(
            title="権限エラー",
            description=f"**{channel.mention}** のメッセージを読み取る権限がありません。\n"
                        "ボットに「**メッセージを読む**」権限（または「チャンネルを見る」権限）を与えてください。",
            color=0xFF0000
        )
        await ctx.reply(embed=embed, ephemeral=True)
        return
    # 権限チェックここまで

    reading_channels[ctx.guild.id] = channel.id
    
    embed = discord.Embed(
        title="読み上げチャンネル設定",
        description=f"**{channel.mention}** を読み上げチャンネルに設定しました。",
        color=0xADD8E6
    )
    embed.set_footer(text=f"設定者: {ctx.author.display_name}", icon_url=ctx.author.avatar.url if ctx.author.avatar else None)
    await ctx.reply(embed=embed, ephemeral=False)

@bot.hybrid_command(name="status", description="Botの現在の稼働状況を表示します。", aliases=["st"])
async def status(ctx: commands.Context):
    """Botの現在のステータス情報を表示します。"""
    guild_count = len(bot.guilds)
    total_members = sum(guild.member_count for guild in bot.guilds)
    vc_count = len(voice_clients)
    reading_count = len(reading_channels)
    
    bot_ping = round(bot.latency * 1000)

    cpu_usage = psutil.cpu_percent(interval=1)
    ram_usage = psutil.virtual_memory().percent
    
    # VRAM表示はRVC機能削除に伴いN/A
    vram_usage_percent = "N/A (RVC機能なし)" 

    cpu_bar = create_progress_bar(cpu_usage)
    ram_bar = create_progress_bar(ram_usage)
    vram_bar = str(vram_usage_percent) 

    embed = discord.Embed(title="読み上げBOT ステータス", color=0x00CCFF) # エメラルドグリーン
    embed.set_thumbnail(url=bot.user.avatar.url if bot.user.avatar else None) # ボットのアイコンを設定
    embed.add_field(name="サーバー情報", value="", inline=False)
    embed.add_field(name="導入サーバー数", value=f"**{guild_count}** サーバー", inline=True)
    embed.add_field(name="合計ユーザー数", value=f"**{total_members}** 人", inline=True)

    embed.add_field(name="VC情報", value="", inline=False)
    embed.add_field(name="VC接続中", value=f"**{vc_count}** サーバー", inline=True)
    embed.add_field(name="読み上げチャンネル", value=f"**{reading_count}** チャンネル", inline=True)

    embed.add_field(name="システム使用率", value="", inline=False)
    embed.add_field(name="ボットPing", value=f"**{bot_ping}**ms", inline=False) # Pingをこちらに移動
    embed.add_field(name="CPU", value=cpu_bar, inline=False)
    embed.add_field(name="RAM", value=ram_bar, inline=False)

    embed.set_footer(text=f"最終更新: {time.strftime('%Y/%m/%d %H:%M:%S')}") # フッターに更新日時を追加

    await ctx.reply(embed=embed)

@bot.hybrid_command(name="setspeed", description="読み上げ速度を設定します。(-50% から +200% の範囲)", aliases=["speed"])
@app_commands.describe(speed="速度をパーセントで指定してください (-50 から 200)")
async def setspeed(ctx: commands.Context, speed: int):
    """ユーザーごとの読み上げ速度を設定します。"""
    # 速度の範囲をチェック
    if not (-50 <= speed <= 200):
        embed = discord.Embed(
            title="無効な速度",
            description="速度は **-50% から +200%** の範囲で指定してください。",
            color=0xFFA500 # オレンジ色
        )
        await ctx.reply(embed=embed, ephemeral=True)
        return

    # ユーザーのIDに基づいて速度を保存
    set_user_speed(ctx.author.id, float(speed))
    
    embed = discord.Embed(
        title="読み上げ速度を設定しました",
        description=f"あなたの読み上げ速度: **{speed:+d}%**", # +d で符号を強制表示
        color=0x00BFFF # ディープスカイブルー
    )
    embed.set_footer(text=f"設定者: {ctx.author.display_name}", icon_url=ctx.author.avatar.url if ctx.author.avatar else None)
    await ctx.reply(embed=embed, ephemeral=False)

    if hasattr(bot, 'gui_app'):
        bot.gui_app.log_output.insert(tk.END, f"Bot: ユーザー({ctx.author.display_name})の読み上げ速度を{speed}%に設定しました。\n")
        bot.gui_app.log_output.see(tk.END)

@bot.hybrid_command(name="add_word", description="サーバー専用辞書に単語を追加します。", aliases=["add"])
@app_commands.describe(original="元の語句", reading="読み")
async def add_word(ctx: commands.Context, original: str, reading: str):
    """サーバー専用辞書に単語を追加します。"""
    if not ctx.guild:
        # Embed for not in guild
        embed = discord.Embed(
            title="エラー",
            description="このコマンドはサーバーでのみ使用できます。",
            color=0xFF0000
        )
        await ctx.reply(embed=embed, ephemeral=True)
        return

    server_dict = load_server_dictionary(ctx.guild.id)
    server_dict[original] = reading
    save_server_dictionary(ctx.guild.id, server_dict)
    
    embed = discord.Embed(
        title="辞書に単語を追加しました",
        description=f"元の語句: **{original}**\n読み: **{reading}**",
        color=0x00FF00 # 緑色
    )
    embed.set_footer(text=f"サーバー: {ctx.guild.name}")
    await ctx.reply(embed=embed)

    if hasattr(bot, 'gui_app'):
        bot.gui_app.log_output.insert(tk.END, f"Bot: サーバー辞書({ctx.guild.name})に「{original}」:「{reading}」を追加しました。\n")
        # ここを修正: self ではなく bot.gui_app を使う
        bot.gui_app.log_output.see(tk.END) # 修正箇所

@bot.hybrid_command(name="remove_word", description="サーバー専用辞書から単語を削除します。", aliases=["remove", "rm"])
@app_commands.describe(original="削除する元の語句")
async def remove_word(ctx: commands.Context, original: str):
    """サーバー専用辞書から単語を削除します。"""
    if not ctx.guild:
        # Embed for not in guild
        embed = discord.Embed(
            title="エラー",
            description="このコマンドはサーバーでのみ使用できます。",
            color=0xFF0000
        )
        await ctx.reply(embed=embed, ephemeral=True)
        return

    server_dict = load_server_dictionary(ctx.guild.id)
    if original in server_dict:
        del server_dict[original]
        save_server_dictionary(ctx.guild.id, server_dict)
        
        embed = discord.Embed(
            title="辞書から単語を削除しました",
            description=f"削除された語句: **{original}**",
            color=0xFF0000 # 赤色
        )
        embed.set_footer(text=f"サーバー: {ctx.guild.name}")
        await ctx.reply(embed=embed)

        if hasattr(bot, 'gui_app'):
            bot.gui_app.log_output.insert(tk.END, f"Bot: サーバー辞書({ctx.guild.name})から「{original}」を削除しました。\n")
            bot.gui_app.log_output.see(tk.END)
    else:
        # Embed for word not found
        embed = discord.Embed(
            title="見つかりません",
            description=f"辞書に「**{original}**」は見つかりませんでした。",
            color=0xFFA500
        )
        await ctx.reply(embed=embed)

@bot.hybrid_command(name="show_dict", description="サーバー専用辞書の内容を表示します。", aliases=["show"])
async def show_dict(ctx: commands.Context):
    """サーバー専用辞書の内容を表示します。"""
    if not ctx.guild:
        # Embed for not in guild
        embed = discord.Embed(
            title="エラー",
            description="このコマンドはサーバーでのみ使用できます。",
            color=0xFF0000
        )
        await ctx.reply(embed=embed, ephemeral=True)
        return

    server_dict = load_server_dictionary(ctx.guild.id)
    if not server_dict:
        # Embed for empty dict
        embed = discord.Embed(
            title="辞書は空です",
            description="このサーバーの辞書は空です。",
            color=0x808080 # グレー
        )
        await ctx.reply(embed=embed)
        return

    embed = discord.Embed(title=f"{ctx.guild.name} の辞書", color=0x00FF00)
    description = ""
    for original, reading in server_dict.items():
        description += f"**{original}**: {reading}\n"
    
    if len(description) > 2000: # Discord embed description limit
        description = description[:1997] + "..."

    embed.description = description
    embed.set_footer(text="辞書の内容を表示しています。")
    await ctx.reply(embed=embed)

@bot.event
async def on_message(message: discord.Message):
    """メッセージが送信された際に実行されます。読み上げ処理とコマンド処理を行います。"""
    if message.author.bot:
        return
    if message.guild is None:
        await message.channel.send("このBotはDMでは使用できません。")
        return
    
    gid = message.guild.id

    # コマンドプレフィックスで始まるメッセージは読み上げない
    if message.content.startswith(bot.command_prefix):
        await bot.process_commands(message) # コマンド処理は行う
        return

    if gid not in reading_channels or message.channel.id != reading_channels.get(gid):
        return # 読み上げチャンネルでなければ終了

    if gid not in voice_clients or not voice_clients[gid].is_connected():
        return # VCに接続していなければ終了

    last_active_time[gid] = asyncio.get_event_loop().time() # メッセージ受信時にアクティブ時刻を更新

    if message.content.strip() == "s":
        vc = voice_clients.get(gid)
        if vc and vc.is_playing():
            vc.stop()
        return # 's'は読み上げない

    content_raw = message.content.strip()

    # ここからメッセージのクリーニング処理
    processed_tts_text = content_raw
    processed_tts_text = re.sub(r'\|\|.*?\|\|', 'ネタバレ', processed_tts_text) # ネタバレ
    processed_tts_text = re.sub(r'\*\*|__', '', processed_tts_text) # 太字、下線
    processed_tts_text = re.sub(r'\*|_', '', processed_tts_text) # イタリック
    processed_tts_text = re.sub(r'~~', '', processed_tts_text) # 取り消し線
    processed_tts_text = re.sub(r'`(.*?)`', r'\1', processed_tts_text) # インラインコード
    processed_tts_text = re.sub(r'```.*?```', '', processed_tts_text, flags=re.DOTALL) # コードブロック
    processed_tts_text = re.sub(r'<a?:[a-zA-Z0-9_]+:[0-9]+>', '', processed_tts_text) # カスタム絵文字
    processed_tts_text = re.sub(r'<@!?([0-9]+)>', '', processed_tts_text) # メンション
    processed_tts_text = re.sub(r'<#([0-9]+)>', '', processed_tts_text) # チャンネルリンク
    processed_tts_text = re.sub(r'<@&([0-9]+)>', '', processed_tts_text) # ロールメンション
    processed_tts_text = re.sub(r'https?://\S+', 'URL', processed_tts_text) # URL
    processed_tts_text = re.sub(r'\s+', ' ', processed_tts_text).strip() # 連続する空白を一つに

    # 絵文字の判定と置き換え
    if re.fullmatch(r'<a?:\w+:\d+>', content_raw): # カスタム絵文字のみのメッセージ
        txt = "サーバー絵文字"
    elif re.fullmatch(r'[\U00010000-\U0010FFFF]+', content_raw): # ユニコード絵文字のみのメッセージ
        txt = "絵文字"
    else:
        txt = processed_tts_text
        if message.attachments:
            txt += " 添付ファイル"
        if message.stickers:
            txt += " スタンプ"
        if len(txt) > 300:
            txt = txt[:300] + " 以下省略"
        
        # サーバー辞書とグローバル辞書を適用
        txt = apply_dictionary(txt, gid) 

    if not txt: # 処理後のテキストが空の場合（例：メンションだけのメッセージ）
        return

    try:
        audio = await generate_tts(txt, message.author.id, message.guild.id)
    except Exception as e:
        print(f"TTSエラー: {e}")
        if hasattr(bot, 'gui_app'):
            bot.gui_app.log_output.insert(tk.END, f"エラー: TTS生成中にエラーが発生しました: {e}\n")
            bot.gui_app.log_output.see(tk.END)
        return
    
    play_audio(gid, audio)

    # on_message内でbot.process_commands(message)を呼ぶことで、ハイブリッドコマンドを含め全てのコマンドが動作するようになります
    await bot.process_commands(message)

import math 

# ── Tkinter GUI クラス ──
class BotGUI:
    def __init__(self, master, bot_instance):
        self.master = master
        self.bot = bot_instance
        self.bot.gui_app = self
        master.title("読み上げBOT 管理画面")
        master.geometry("1000x700")

        self.notebook = ttk.Notebook(master)
        self.notebook.pack(pady=10, expand=True, fill="both")

        self.create_dashboard_tab()
        self.create_global_dict_tab()
        self.create_settings_tab()
        self.create_log_tab()

        self.master.protocol("WM_DELETE_WINDOW", self.on_closing)

        # GUIの定期更新タスクを開始
        self.update_gui_tasks()

    def update_gui_tasks(self):
        """GUIの表示を定期的に更新します。"""
        self.master.after(5000, self.update_dashboard_display) # ダッシュボードを5秒ごとに更新
        self.master.after(100, self.update_gui_tasks) # 次の更新をスケジュール

    def create_dashboard_tab(self):
        """ダッシュボードタブを作成します。"""
        self.dashboard_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.dashboard_frame, text="ダッシュボード")

        self.status_label = ttk.Label(self.dashboard_frame, text="Botステータス: 初期化中...")
        self.status_label.pack(pady=10)

        self.ping_label = ttk.Label(self.dashboard_frame, text="Ping: N/A")
        self.ping_label.pack(pady=5)

        self.guild_count_label = ttk.Label(self.dashboard_frame, text="導入サーバー数: N/A")
        self.guild_count_label.pack(pady=5)
        
        self.member_count_label = ttk.Label(self.dashboard_frame, text="合計ユーザー数: N/A")
        self.member_count_label.pack(pady=5)

        ttk.Label(self.dashboard_frame, text="導入サーバー一覧:").pack(pady=10)
        self.guild_tree = ttk.Treeview(self.dashboard_frame, columns=("ID", "メンバー数", "VC接続中", "読み上げチャンネル"), show="headings")
        self.guild_tree.heading("ID", text="ID")
        self.guild_tree.heading("メンバー数", text="メンバー数")
        self.guild_tree.heading("VC接続中", text="VC接続中")
        self.guild_tree.heading("読み上げチャンネル", text="読み上げチャンネル")
        self.guild_tree.column("ID", width=150, anchor=tk.W)
        self.guild_tree.column("メンバー数", width=80, anchor=tk.CENTER)
        self.guild_tree.column("VC接続中", width=100, anchor=tk.CENTER)
        self.guild_tree.column("読み上げチャンネル", width=150, anchor=tk.W)
        self.guild_tree.pack(expand=True, fill="both", padx=10, pady=5)

        self.update_dashboard_display()

    def update_dashboard_display(self):
        """ダッシュボードの表示を更新します。"""
        if not self.bot.is_ready():
            self.status_label.config(text="Botステータス: オフライン")
            self.ping_label.config(text="Ping: N/A")
            self.guild_count_label.config(text="導入サーバー数: N/A")
            self.member_count_label.config(text="合計メンバー数: N/A")
            for item in self.guild_tree.get_children():
                self.guild_tree.delete(item)
            return

        self.status_label.config(text="Botステータス: オンライン")
        if self.bot.is_ready() and not math.isinf(self.bot.latency):
            ping_ms = round(self.bot.latency * 1000)
            self.ping_label.config(text=f"Ping: {ping_ms}ms")
        else:
            # Botがまだ準備できていないか、Pingが取得できない場合
            self.ping_label.config(text="Ping: N/A")
        self.guild_count_label.config(text=f"導入サーバー数: {len(self.bot.guilds)}")
        self.member_count_label.config(text=f"合計メンバー数: {sum(g.member_count for g in self.bot.guilds)}")

        for item in self.guild_tree.get_children():
            self.guild_tree.delete(item)
        for guild in self.bot.guilds:
            vc_connected = "はい" if guild.id in voice_clients and voice_clients[guild.id].is_connected() else "いいえ"
            reading_channel_name = "未設定"
            if guild.id in reading_channels:
                channel = self.bot.get_channel(reading_channels[guild.id])
                if channel:
                    reading_channel_name = channel.name
                else:
                    reading_channel_name = f"不明 ({reading_channels[guild.id]})"
            
            self.guild_tree.insert("", "end", text=guild.name, values=(
                guild.id,
                guild.member_count,
                vc_connected,
                reading_channel_name
            ))

    def create_global_dict_tab(self):
        """グローバル辞書タブを作成します。"""
        self.global_dict_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.global_dict_frame, text="グローバル辞書")

        self.global_dict_tree = ttk.Treeview(self.global_dict_frame, columns=("Original", "Reading"), show="headings")
        self.global_dict_tree.heading("Original", text="元の語句")
        self.global_dict_tree.heading("Reading", text="読み")
        self.global_dict_tree.pack(expand=True, fill="both", padx=10, pady=10)

        self.global_dict_tree.bind("<ButtonRelease-1>", self.select_global_dict_item)

        input_frame = ttk.Frame(self.global_dict_frame)
        input_frame.pack(pady=5)

        ttk.Label(input_frame, text="元の語句:").grid(row=0, column=0, padx=5, pady=2, sticky="w")
        self.global_original_entry = ttk.Entry(input_frame, width=30)
        self.global_original_entry.grid(row=0, column=1, padx=5, pady=2)

        ttk.Label(input_frame, text="読み:").grid(row=1, column=0, padx=5, pady=2, sticky="w")
        self.global_reading_entry = ttk.Entry(input_frame, width=30)
        self.global_reading_entry.grid(row=1, column=1, padx=5, pady=2)

        button_frame = ttk.Frame(self.global_dict_frame)
        button_frame.pack(pady=5)

        ttk.Button(button_frame, text="追加", command=self.add_global_dict_entry).grid(row=0, column=0, padx=5)
        ttk.Button(button_frame, text="更新", command=self.update_global_dict_entry).grid(row=0, column=1, padx=5)
        ttk.Button(button_frame, text="削除", command=self.remove_global_dict_entry).grid(row=0, column=2, padx=5)

        self.update_global_dict_display()

    def update_global_dict_display(self):
        """グローバル辞書の表示を更新します。"""
        for item in self.global_dict_tree.get_children():
            self.global_dict_tree.delete(item)
        for original, reading in global_dict.items():
            self.global_dict_tree.insert("", "end", values=(original, reading))
        self.global_original_entry.delete(0, tk.END)
        self.global_reading_entry.delete(0, tk.END)

    def select_global_dict_item(self, event):
        """グローバル辞書ツリービューの項目が選択されたときにエントリーに設定します。"""
        selected_item = self.global_dict_tree.focus()
        if selected_item:
            values = self.global_dict_tree.item(selected_item, "values")
            self.global_original_entry.delete(0, tk.END)
            self.global_original_entry.insert(0, values[0])
            self.global_reading_entry.delete(0, tk.END)
            self.global_reading_entry.insert(0, values[1])

    def add_global_dict_entry(self):
        """グローバル辞書に新しいエントリを追加します。"""
        original = self.global_original_entry.get().strip()
        reading = self.global_reading_entry.get().strip()
        if original and reading:
            if original in global_dict:
                messagebox.showwarning("警告", f"'{original}' は既に辞書に存在します。更新する場合は更新ボタンを使用してください。")
                return
            global_dict[original] = reading
            save_global_dictionary(global_dict)
            self.update_global_dict_display()
            self.log_output.insert(tk.END, f"GUI: グローバル辞書に「{original}」:「{reading}」を追加しました。\n")
            self.log_output.see(tk.END)
        else:
            messagebox.showwarning("警告", "元の語句と読みの両方を入力してください。")

    def update_global_dict_entry(self):
        """グローバル辞書のエントリを更新します。"""
        original = self.global_original_entry.get().strip()
        reading = self.global_reading_entry.get().strip()
        if original and reading:
            if original in global_dict:
                global_dict[original] = reading
                save_global_dictionary(global_dict)
                self.update_global_dict_display()
                self.log_output.insert(tk.END, f"GUI: グローバル辞書の「{original}」を「{reading}」に更新しました。\n")
                self.log_output.see(tk.END)
            else:
                messagebox.showwarning("警告", f"'{original}' は辞書に見つかりません。追加する場合は追加ボタンを使用してください。")
        else:
            messagebox.showwarning("警告", "元の語句と読みの両方を入力してください。")

    def remove_global_dict_entry(self):
        """グローバル辞書からエントリを削除します。"""
        original = self.global_original_entry.get().strip()
        if original:
            if original in global_dict:
                confirm = messagebox.askyesno("確認", f"'{original}' を辞書から削除しますか？")
                if confirm:
                    del global_dict[original]
                    save_global_dictionary(global_dict)
                    self.update_global_dict_display()
                    self.log_output.insert(tk.END, f"GUI: グローバル辞書から「{original}」を削除しました。\n")
                    self.log_output.see(tk.END)
            else:
                messagebox.showwarning("警告", f"'{original}' は辞書に見つかりません。")
        else:
            messagebox.showwarning("警告", "削除する元の語句を入力してください。")

    def create_settings_tab(self):
        """設定タブを作成します。"""
        self.settings_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.settings_frame, text="設定")
        
        ttk.Label(self.settings_frame, text="読み上げ速度はユーザーごとに設定されます。", foreground="red", font=("", 12, "bold")).pack(pady=10)
        ttk.Label(self.settings_frame, text="`/setspeed` コマンドで個人の読み上げ速度を設定してください。", font=("", 10)).pack(pady=5)
        ttk.Label(self.settings_frame, text="（GUIからのグローバル速度設定は廃止されました）", font=("", 10)).pack(pady=5)
        
    def create_log_tab(self):
        """ログタブを作成します。"""
        self.log_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.log_frame, text="ログ")

        self.log_output = scrolledtext.ScrolledText(self.log_frame, wrap=tk.WORD, width=100, height=30)
        self.log_output.pack(expand=True, fill="both", padx=10, pady=10)
        self.log_output.config(state='disabled') # 読み取り専用にする

        # Botの標準出力と標準エラー出力をログウィジェットにリダイレクト
        sys.stdout = TextRedirector(self.log_output, "stdout")
        sys.stderr = TextRedirector(self.log_output, "stderr")

    def on_closing(self):
        """GUIウィンドウが閉じられたときに実行されます。Botを停止します。"""
        if messagebox.askokcancel("終了確認", "Botを停止してアプリケーションを終了しますか？"):
            self.master.destroy()
            if self.bot.is_ready():
                print("Botをシャットダウンしています...")
                asyncio.run_coroutine_threadsafe(self.bot.close(), self.bot.loop).result()
            print("アプリケーションを終了しました。")
            os._exit(0) # 強制終了

class TextRedirector(object):
    """標準出力/エラー出力をTkinterのTextウィジェットにリダイレクトするクラス。"""
    def __init__(self, widget, tag="stdout"):
        self.widget = widget
        self.tag = tag

    def write(self, text):
        self.widget.config(state='normal')
        self.widget.insert(tk.END, text, (self.tag,))
        self.widget.see(tk.END)
        self.widget.config(state='disabled')

    def flush(self):
        pass # TkinterのTextウィジェットではflushは不要

# Botを別スレッドで実行
def run_bot():
    if BOT_TOKEN is None:
        print("エラー: BOT_TOKENが設定されていません。'.env'ファイルを確認してください。")
        sys.exit(1)
    try:
        bot.run(BOT_TOKEN)
    except discord.errors.LoginFailure:
        print("エラー: BOT_TOKENが無効です。'.env'ファイルを確認してください。")
        sys.exit(1)

if __name__ == "__main__":
    # GUIスレッドでTkinterウィンドウを作成し、Botスレッドを起動
    root = tk.Tk()
    gui = BotGUI(root, bot)

    bot_thread = threading.Thread(target=run_bot)
    bot_thread.daemon = True # メインスレッド(GUI)終了時にBotスレッドも終了
    bot_thread.start()

    root.mainloop()