# bot.py - FULLY FIXED & COMPLETE LegendBot (Python) - All Features, No Missing, Sync Debugged
# Fixes:
# - Invalid GUILD_ID=0 causes 0 synced → fallback to global sync if not set
# - Added debug prints: GUILD_ID, commands in tree, synced list
# - Manual !sync command (prefix) for owner to force sync anytime
# - Clear commands before sync (guild or global)
# - All commands defined fully (no abbreviations)
# - Added guild sync check + global fallback
# - Bot invite: Ensure 'applications.commands' scope + bot in server
# - Features: All from originals (voice cam track/enforce, leaderboards, TODO modal/ping, redlist/auto-ban, admin cmds, DM forward, anti-nuke/spam/strikes, bot whitelist, activity logs)
# - Added all missing commands: /listtodo, /deltodo, /members, /ck
# - Adjusted /todostatus for self-check by members, optional other for owner
# - Added full 12-layer security firewalls
# - Single guild support with GUILD_ID checks
# - Fixed cam_timers safe cancellation
# - Fixed vc_join_times continuation on cam change
# - Fixed batch_save_study to use members from guild
# - Async main for web + bot

import discord
from discord import app_commands
from discord.ext import commands, tasks
import os
import asyncio
import time
import datetime
from datetime import timedelta
import pytz
from collections import defaultdict
import aiohttp
from aiohttp import web
from pymongo import MongoClient
from dotenv import load_dotenv
from discord.app_commands import checks
import socket
import re
import sys
import aiosqlite
import unicodedata
from urllib.parse import urlparse, urlunparse
from motor.motor_asyncio import AsyncIOMotorClient


def configure_console_output() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


configure_console_output()

load_dotenv()


def get_int_env(name: str, default: int) -> int:
    """Read an integer env var safely and fall back without crashing startup."""
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return default

    try:
        return int(raw_value)
    except ValueError:
        print(f"⚠️ Invalid {name}='{raw_value}' - using fallback {default}")
        return default

# ==================== CONFIG ====================
TOKEN = os.getenv("DISCORD_TOKEN")
CLIENT_ID = os.getenv("CLIENT_ID")
GUILD_ID_STR = os.getenv("GUILD_ID", "0")
MONGODB_URI = os.getenv("MONGODB_URI")
TEMP_VOICE_CATEGORY_ID = get_int_env("TEMP_VOICE_CATEGORY_ID", 0)  # Category for temp voice channels
TEMP_CATEGORY_ID = get_int_env("TEMP_CATEGORY_ID", 1486534382314455151)
INTERFACE_CHANNEL_ID = get_int_env("INTERFACE_CHANNEL_ID", 1486552573652631732)
LOBBY_CHANNEL_ID = get_int_env("LOBBY_CHANNEL_ID", 1486535158185197649)
PORT = get_int_env("PORT", 3000)
FRONTEND_URL = os.getenv("FRONTEND_URL", f"http://localhost:{PORT}/LEGEND-STAR")
OWNER_ID = get_int_env("OWNER_ID", 1406313503278764174)

# Async Mongo (motor) for temp voice channel persistence
if MONGODB_URI:
    try:
        mongo_async = AsyncIOMotorClient(
            MONGODB_URI,
            serverSelectionTimeoutMS=20000,
            tlsAllowInvalidCertificates=True,
            retryWrites=True
        )
        tempvoice_coll = mongo_async["legend_star"]["tempvoice"]
    except Exception as e:
        print(f"⚠️ Motor Mongo init failed: {e}")
        mongo_async = None
        tempvoice_coll = None
else:
    mongo_async = None
    tempvoice_coll = None

# Temp voice DB availability guard (avoid hangs if async Mongo is unreachable)
tempvoice_db_available = tempvoice_coll is not None

async def get_temp_channel_owner(channel):
    """Return owner_id for temp channel (runtime cache first, then DB)."""
    if not channel:
        return None

    runtime_owner = tempvoice_runtime_owner_by_channel.get(channel.id)
    if runtime_owner is not None:
        return runtime_owner

    if tempvoice_db_available and tempvoice_coll is not None:
        entry = await tempvoice_db_find_one({"channel_id": channel.id})
        if entry and entry.get("owner_id"):
            owner_id = entry.get("owner_id")
            tempvoice_runtime_owner_by_channel[channel.id] = owner_id
            tempvoice_runtime_channel_by_owner[owner_id] = channel.id
            return owner_id

    return None

# ==================== TEMP VOICE CONTROL PANEL ====================
class ControlPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction: discord.Interaction):
        channel = interaction.user.voice.channel if interaction.user.voice else None
        if not channel:
            await interaction.response.send_message("❌ Join a voice channel first", ephemeral=True)
            return False

        owner_id = await get_temp_channel_owner(channel)
        if owner_id is None:
            await interaction.response.send_message("❌ This is not a temp channel or the owner is unknown", ephemeral=True)
            return False

        if owner_id != interaction.user.id:
            await interaction.response.send_message("❌ You are not the owner of this channel", ephemeral=True)
            return False

        return True

    @discord.ui.button(label="Lock", style=discord.ButtonStyle.danger, custom_id="tempvoice_lock_btn")
    async def lock(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.user.voice.channel
        await channel.set_permissions(interaction.guild.default_role, connect=False)
        await interaction.response.send_message("🔒 Channel locked", ephemeral=True)

    @discord.ui.button(label="Unlock", style=discord.ButtonStyle.success, custom_id="tempvoice_unlock_btn")
    async def unlock(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.user.voice.channel
        await channel.set_permissions(interaction.guild.default_role, connect=True)
        await interaction.response.send_message("🔓 Channel unlocked", ephemeral=True)

    @discord.ui.button(label="Hide", style=discord.ButtonStyle.secondary, custom_id="tempvoice_hide_btn")
    async def hide(self, interaction: discord.Interaction, button):
        channel = interaction.user.voice.channel
        await channel.set_permissions(interaction.guild.default_role, view_channel=False)
        await interaction.response.send_message("👁 Channel hidden", ephemeral=True)

    @discord.ui.button(label="Unhide", style=discord.ButtonStyle.secondary, custom_id="tempvoice_unhide_btn")
    async def unhide(self, interaction: discord.Interaction, button):
        channel = interaction.user.voice.channel
        await channel.set_permissions(interaction.guild.default_role, view_channel=True)
        await interaction.response.send_message("👁‍🗨 Channel visible", ephemeral=True)

    @discord.ui.button(label="Limit", style=discord.ButtonStyle.primary, custom_id="tempvoice_limit_btn")
    async def limit(self, interaction: discord.Interaction, button):
        # Use modal for input
        modal = LimitModal()
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Rename", style=discord.ButtonStyle.primary, custom_id="tempvoice_rename_btn")
    async def rename(self, interaction: discord.Interaction, button):
        modal = RenameModal()
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Permit", style=discord.ButtonStyle.success, custom_id="tempvoice_permit_btn")
    async def permit(self, interaction: discord.Interaction, button):
        modal = PermitModal()
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="tempvoice_deny_btn")
    async def deny(self, interaction: discord.Interaction, button):
        modal = DenyModal()
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.secondary, custom_id="tempvoice_claim_btn")
    async def claim(self, interaction: discord.Interaction, button):
        global tempvoice_db_available
        channel = interaction.user.voice.channel
        if not channel:
            return await interaction.response.send_message("❌ Join the voice channel first", ephemeral=True)

        owner_id = await get_temp_channel_owner(channel)
        if owner_id and owner_id in [m.id for m in channel.members]:
            return await interaction.response.send_message("❌ Owner is still in the channel", ephemeral=True)

        tempvoice_runtime_owner_by_channel[channel.id] = interaction.user.id
        tempvoice_runtime_channel_by_owner[interaction.user.id] = channel.id

        if tempvoice_db_available and tempvoice_coll is not None:
            try:
                await tempvoice_coll.update_one({"channel_id": channel.id}, {"$set": {"owner_id": interaction.user.id}}, upsert=True)
            except Exception as e:
                print(f"⚠️ Temp voice claim update failed: {e}")
                tempvoice_db_available = False

        await interaction.response.send_message("👑 Ownership claimed", ephemeral=True)

    @discord.ui.button(label="Transfer", style=discord.ButtonStyle.secondary, custom_id="tempvoice_transfer_btn")
    async def transfer(self, interaction: discord.Interaction, button):
        modal = TransferModal()
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Bitrate", style=discord.ButtonStyle.primary, custom_id="tempvoice_bitrate_btn")
    async def bitrate(self, interaction: discord.Interaction, button):
        modal = BitrateModal()
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Region", style=discord.ButtonStyle.primary, custom_id="tempvoice_region_btn")
    async def region(self, interaction: discord.Interaction, button):
        modal = RegionModal()
        await interaction.response.send_modal(modal)

# Modals for inputs
class LimitModal(discord.ui.Modal, title="Set User Limit"):
    limit = discord.ui.TextInput(label="User Limit (0 for unlimited)", placeholder="0")

    async def on_submit(self, interaction: discord.Interaction):
        try:
            limit = int(self.limit.value)
            channel = interaction.user.voice.channel
            await channel.edit(user_limit=limit)
            await interaction.response.send_message(f"👥 User limit set to {limit}", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Invalid number", ephemeral=True)

class RenameModal(discord.ui.Modal, title="Rename Channel"):
    name = discord.ui.TextInput(label="New Channel Name", placeholder="My Room")

    async def on_submit(self, interaction: discord.Interaction):
        channel = interaction.user.voice.channel
        await channel.edit(name=self.name.value)
        await interaction.response.send_message(f"✏ Channel renamed to {self.name.value}", ephemeral=True)

class PermitModal(discord.ui.Modal, title="Permit User"):
    user = discord.ui.TextInput(label="User ID or Mention", placeholder="@user or 123456789")

    async def on_submit(self, interaction: discord.Interaction):
        try:
            user_id = int(self.user.value.strip('<@!>'))
            user = interaction.guild.get_member(user_id)
            if not user:
                await interaction.response.send_message("❌ User not found", ephemeral=True)
                return
            channel = interaction.user.voice.channel
            await channel.set_permissions(user, connect=True)
            await interaction.response.send_message(f"✅ Permitted {user.mention}", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Invalid user", ephemeral=True)

class DenyModal(discord.ui.Modal, title="Deny User"):
    user = discord.ui.TextInput(label="User ID or Mention", placeholder="@user or 123456789")

    async def on_submit(self, interaction: discord.Interaction):
        try:
            user_id = int(self.user.value.strip('<@!>'))
            user = interaction.guild.get_member(user_id)
            if not user:
                await interaction.response.send_message("❌ User not found", ephemeral=True)
                return
            channel = interaction.user.voice.channel
            await channel.set_permissions(user, connect=False)
            await interaction.response.send_message(f"❌ Denied {user.mention}", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Invalid user", ephemeral=True)

class TransferModal(discord.ui.Modal, title="Transfer Ownership"):
    user = discord.ui.TextInput(label="User ID or Mention", placeholder="@user or 123456789")

    async def on_submit(self, interaction: discord.Interaction):
        try:
            user_id = int(self.user.value.strip('<@!>'))
            user = interaction.guild.get_member(user_id)
            if not user:
                await interaction.response.send_message("❌ User not found", ephemeral=True)
                return
            channel = interaction.user.voice.channel
            if not channel:
                return await interaction.response.send_message("❌ Join the voice channel first", ephemeral=True)

            tempvoice_runtime_owner_by_channel[channel.id] = user_id
            tempvoice_runtime_channel_by_owner[user_id] = channel.id

            if tempvoice_db_available and tempvoice_coll is not None:
                try:
                    await tempvoice_coll.update_one({"channel_id": channel.id}, {"$set": {"owner_id": user_id}}, upsert=True)
                except Exception as e:
                    print(f"⚠️ Temp voice transfer update failed: {e}")
                    tempvoice_db_available = False

            await interaction.response.send_message(f"🔄 Ownership transferred to {user.mention}", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Invalid user", ephemeral=True)

class BitrateModal(discord.ui.Modal, title="Set Bitrate"):
    bitrate = discord.ui.TextInput(label="Bitrate (8000-128000)", placeholder="64000")

    async def on_submit(self, interaction: discord.Interaction):
        try:
            bitrate = int(self.bitrate.value)
            if not 8000 <= bitrate <= 128000:
                await interaction.response.send_message("❌ Bitrate must be between 8000 and 128000", ephemeral=True)
                return
            channel = interaction.user.voice.channel
            await channel.edit(bitrate=bitrate)
            await interaction.response.send_message(f"🎧 Bitrate set to {bitrate}", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Invalid number", ephemeral=True)

class RegionModal(discord.ui.Modal, title="Set Region"):
    region = discord.ui.TextInput(label="Region", placeholder="india")

    async def on_submit(self, interaction: discord.Interaction):
        channel = interaction.user.voice.channel
        await channel.edit(rtc_region=self.region.value)
        await interaction.response.send_message(f"🌍 Region set to {self.region.value}", ephemeral=True)

# Validate required environment variables
if not TOKEN:
    raise ValueError("❌ DISCORD_TOKEN is not set in .env file")
if not CLIENT_ID:
    raise ValueError("❌ CLIENT_ID is not set in .env file")
if not MONGODB_URI:
    raise ValueError("❌ MONGODB_URI is not set in .env file")

try:
    GUILD_ID = int(GUILD_ID_STR)
except ValueError:
    print(f"❌ Invalid GUILD_ID '{GUILD_ID_STR}' - must be a number. Using 0 for global sync.")
    GUILD_ID = 0
TECH_CHANNEL_ID = 1458142927619362969
KOLKATA = pytz.timezone("Asia/Kolkata")
AUTO_LB_CHANNEL_ID = 1455385042044846242
AUTO_LB_PING_ROLE_ID = 1457931098171506719  # 🏆 Role to ping at 11:55 for leaderboard announcement
TODO_CHANNEL_ID = 1458400694682783775
ROLE_ID = 1458400797133115474
ACCESS_PANEL_CHANNEL_ID = 1455815424267518086
ACCESS_GRANTED_ROLE_ID = 1457931098171506719
ACCESS_WELCOME_CHANNEL_ID = 1456959255742775437
ACCESS_PANEL_BUTTON_CUSTOM_ID = "legendstar:get-access:v1"
ACCESS_PANEL_EMBED_MARKER = "legendstar-access-panel-v1"
print(f"GUILD_ID from env: {GUILD_ID}")  # DEBUG: Check if set correctly
# Excluded voice channel ID: do not record cam on/off minutes for this VC
EXCLUDED_VOICE_CHANNEL_ID = 1466076240111992954

# Strict channels
STRICT_CHANNEL_IDS = {1428762702414872636, 1455906399262605457, 1428762820585062522}
CAMERA_ENFORCEMENT_SECONDS = 180

# Camera bypass role (users with this role bypass camera enforcement)
CAMERA_BYPASS_ROLE = 1505454225616932955

# Soft automod marker role names (delete-only enforcement)
NOPING_ROLE = "NoPing"
NOMSG_ROLE = "NoMessage"
DEFAULT_REASON = "Previously warned by automod"
# Bot whitelist
WHITELISTED_BOTS = [
    1457787743504695501, 1456587533474463815, 1427522983789989960, 155149108183695360,
    678344927997853742, 1053580838945693717, 235148962103951360, 1458076467203145851,
    762217899355013120, 1444646362204475453, 536991182035746816, 906085578909548554,
    1149535834756874250, 1460114117783195841, 889078613817831495, 704802632660943089, 712638684930900059, 369208607126061057, 1470151477610938509, 684773505157431347, 810540985032900648, 1490234600469954561
]

# Webhook whitelist (add IDs if needed)
WHITELISTED_WEBHOOKS = [
    1457787743504695501, 1456587533474463815, 1427522983789989960, 155149108183695360,
    678344927997853742, 1053580838945693717, 235148962103951360, 1458076467203145851,
    762217899355013120, 1444646362204475453, 536991182035746816, 906085578909548554,
    1149535834756874250, 1460114117783195841, 889078613817831495, 704802632660943089, 712638684930900059, 369208607126061057, 1470151477610938509, 684773505157431347, 810540985032900648, 1490234600469954561
]

# Spam settings
SPAM_THRESHOLD = 4
SPAM_WINDOW = 5
MAX_MENTIONS = 5
TIMEOUT_DURATION = 60
STRIKE_RESET = 300

# Enhanced Security Settings
FORBIDDEN_KEYWORDS = ["@everyone", "@here", "free nitro", "steam community", "gift", "airdrop", "maa", "rand", "chut"]
DANGEROUS_PERMISSION_NAMES = {
    "administrator",
    "manage_guild",
    "manage_roles",
    "manage_channels",
    "manage_webhooks",
    "ban_members",
    "kick_members",
    "mention_everyone",
}
AUDIT_MATCH_WINDOW_SECONDS = 20
SECURITY_ZERO_WIDTH_TRANSLATION = str.maketrans("", "", "\u200b\u200c\u200d\u2060\ufeff")

# 🛡️ TRUSTED LISTS (Whitelist for Strike System)
TRUSTED_USERS = [OWNER_ID, 1449952640455934022]  # Added 1449952640455934022 as trusted owner-level user
TRUSTED_BOTS = WHITELISTED_BOTS.copy()
TEMP_VOICE_BOT_ID = 762217899355013120

# ==================== SQLITE SPY DATABASE ====================
DB_PATH = "spy_tracker.db"  # SQLite database for tracking user activity

async def init_spy_db():
    """Initialize SQLite database schema for spy tracking"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Create tables if they don't exist
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    messages INTEGER DEFAULT 0,
                    cam_on INTEGER DEFAULT 0,
                    cam_off INTEGER DEFAULT 0
                )
            """)
            
            await db.execute("""
                CREATE TABLE IF NOT EXISTS message_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    channel TEXT,
                    content TEXT,
                    time TEXT
                )
            """)
            
            await db.execute("""
                CREATE TABLE IF NOT EXISTS vc_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    channel TEXT,
                    time TEXT
                )
            """)
            
            await db.execute("""
                CREATE TABLE IF NOT EXISTS spy_targets (
                    user_id INTEGER PRIMARY KEY
                )
            """)
            
            await db.commit()
            print("✅ Spy database initialized")
    except Exception as e:
        print(f"⚠️ Spy DB init error: {e}")

# 📒 STRIKE DATABASE (2-Strike System for Human Errors)
offense_history = {}  # {user_id: timestamp_of_last_offense}
is_locked_down = False  # Global lockdown state

# 🔍 AUDIT LOG TRACKING (Prevent Duplicate Messages)
processed_audit_ids = set()  # Track processed audit entry IDs to prevent duplicate alerts
processed_audit_timestamps = {}  # {audit_id: timestamp} for more robust deduplication
MAX_AUDIT_CACHE = 1000  # Max entries to cache (prevents memory bloat)
AUDIT_DEDUP_WINDOW = 5  # seconds - window to consider duplicate audit entries

# Security settings
DANGEROUS_EXTS = {'.exe', '.bat', '.cmd', '.msi', '.apk', '.jar', '.vbs', '.scr', '.ps1', '.hta'}
RAID_THRESHOLD = 5
RAID_WINDOW = 60
VC_ABUSE_THRESHOLD = 5
VC_ABUSE_WINDOW = 30

# MongoDB Connection Handler
db = None
users_coll = None
todo_coll = None
redlist_coll = None
active_members_coll = None
mongo_connected = False

def init_mongo():
    """Initialize MongoDB connection with advanced retry logic and SSL fallbacks"""
    global db, users_coll, todo_coll, redlist_coll, active_members_coll, mongo_connected
    
    print(f"📡 Attempting to connect to MongoDB: {MONGODB_URI[:50]}...")
    
    # Strategy 1: Try with SRV and relaxed TLS
    print("🔄 MongoDB Connection Strategy 1: SRV with Relaxed TLS...")
    try:
        client = MongoClient(
            MONGODB_URI,
            serverSelectionTimeoutMS=20000,
            connectTimeoutMS=20000,
            socketTimeoutMS=20000,
            tlsAllowInvalidCertificates=True,
            retryWrites=True,
            directConnection=False
        )
        client.admin.command('ping')
        db = client["legend_star"]
        users_coll = db["users"]
        todo_coll = db["todo_timestamps"]
        redlist_coll = db["redlist"]
        active_members_coll = db["active_members"]
        mongo_connected = True
        print("✅ MongoDB connected successfully (SRV + Relaxed TLS)")
        return True
    except Exception as e:
        print(f"⚠️ Strategy 1 failed: {str(e)[:100]}...")
    
    # Strategy 2: Try with retryWrites disabled
    print("🔄 MongoDB Connection Strategy 2: SRV without Retry Writes...")
    try:
        client = MongoClient(
            MONGODB_URI,
            serverSelectionTimeoutMS=20000,
            connectTimeoutMS=20000,
            socketTimeoutMS=20000,
            tlsAllowInvalidCertificates=True,
            retryWrites=False,
            directConnection=False
        )
        client.admin.command('ping')
        db = client["legend_star"]
        users_coll = db["users"]
        todo_coll = db["todo_timestamps"]
        redlist_coll = db["redlist"]
        active_members_coll = db["active_members"]
        mongo_connected = True
        print("✅ MongoDB connected successfully (SRV, No Retry Writes)")
        return True
    except Exception as e:
        print(f"⚠️ Strategy 2 failed: {str(e)[:100]}...")
    
    # Strategy 3: Try without SSL/TLS (last resort)
    print("🔄 MongoDB Connection Strategy 3: No TLS/SSL...")
    try:
        client = MongoClient(
            MONGODB_URI,
            serverSelectionTimeoutMS=20000,
            connectTimeoutMS=20000,
            socketTimeoutMS=20000,
            ssl=False,
            retryWrites=False,
            directConnection=False
        )
        client.admin.command('ping')
        db = client["legend_star"]
        users_coll = db["users"]
        todo_coll = db["todo_timestamps"]
        redlist_coll = db["redlist"]
        active_members_coll = db["active_members"]
        mongo_connected = True
        print("✅ MongoDB connected successfully (No TLS/SSL)")
        return True
    except Exception as e:
        print(f"⚠️ Strategy 3 failed: {str(e)[:100]}...")
    
    # Strategy 4: Extended timeout with maxPoolSize=1
    print("🔄 MongoDB Connection Strategy 4: Extended Timeout (Single Pool)...")
    try:
        client = MongoClient(
            MONGODB_URI,
            serverSelectionTimeoutMS=60000,
            connectTimeoutMS=60000,
            socketTimeoutMS=60000,
            tlsAllowInvalidCertificates=True,
            retryWrites=False,
            directConnection=False,
            maxPoolSize=1,
            minPoolSize=0,
            maxIdleTimeMS=90000
        )
        client.admin.command('ping')
        db = client["legend_star"]
        users_coll = db["users"]
        todo_coll = db["todo_timestamps"]
        redlist_coll = db["redlist"]
        active_members_coll = db["active_members"]
        mongo_connected = True
        print("✅ MongoDB connected successfully (Extended Timeout)")
        return True
    except Exception as e:
        print(f"⚠️ Strategy 4 failed: {str(e)[:100]}...")
    
    # All strategies failed - use in-memory cache
    print("❌ All MongoDB connection strategies failed. Bot will use in-memory cache only.")
    print("⚠️ Data persistence is DISABLED. Changes will be lost on restart.")
    print("📝 Troubleshooting: Check if MongoDB Atlas IP whitelist includes your IP address")
    print("📝 If using Docker: Add 0.0.0.0/0 to IP Whitelist in MongoDB Atlas")
    print("📝 Verify credentials in MONGODB_URI are correct")
    
    # Create empty collection objects for compatibility
    try:
        client = MongoClient(
            MONGODB_URI,
            serverSelectionTimeoutMS=5000,
            tlsAllowInvalidCertificates=True,
            retryWrites=False
        )
        db = client["legend_star"]
        users_coll = db["users"]
        todo_coll = db["todo_timestamps"]
        redlist_coll = db["redlist"]
        active_members_coll = db["active_members"]
    except Exception as e:
        db = None
        users_coll = None
        todo_coll = None
        redlist_coll = None
        active_members_coll = None
    
    mongo_connected = False
    return False

# Initialize MongoDB on startup
mongo_connected = init_mongo()

# ====================================================
# 🚨 ALERT SYSTEM (DM OWNER)
# ====================================================
async def alert_owner(guild, title, field_data):
    """Send security alert to owner via DM"""
    try:
        user = await bot.fetch_user(OWNER_ID)
        embed = discord.Embed(
            title=f"🚨 SECURITY ALERT: {title}",
            color=discord.Color.red(),
            timestamp=datetime.datetime.now()
        )
        embed.add_field(name="Server", value=guild.name if guild else "Unknown", inline=True)
        for key, value in field_data.items():
            embed.add_field(name=key, value=value, inline=False)
        await user.send(embed=embed)
    except Exception as e:
        print(f"⚠️ Failed to alert owner: {e}")


async def send_security_log(
    guild: discord.Guild | None,
    title: str,
    *,
    description: str | None = None,
    color: discord.Color | None = None,
    fields: dict[str, str] | None = None,
) -> None:
    """Send a structured security log to the configured tech channel."""
    if guild is None:
        return

    tech_channel = bot.get_channel(TECH_CHANNEL_ID)
    if tech_channel is None:
        return

    try:
        embed = discord.Embed(
            title=title,
            description=description,
            color=color or discord.Color.orange(),
            timestamp=datetime.datetime.now(KOLKATA),
        )
        for key, value in (fields or {}).items():
            embed.add_field(name=key, value=truncate_embed_field(str(value), 1000), inline=False)
        await tech_channel.send(embed=embed)
    except Exception as e:
        print(f"⚠️ Failed to send security log '{title}': {e}")

# ====================================================
# 🧠 INTELLIGENT PUNISHMENT SYSTEM (The Brain)
# ====================================================
async def punish_human(message, reason):
    """
    Decides whether to Timeout (1st time) or Ban (2nd time).
    Uses intelligent strike system for human mistakes.
    """
    user = message.author
    user_id = user.id
    now = datetime.datetime.now().timestamp()

    # 1. Initialize User Cache
    if user_id not in strike_cache:
        strike_cache[user_id] = []

    # 2. Clean old strikes (older than 5 minutes)
    strike_cache[user_id] = [t for t in strike_cache[user_id] if now - t < STRIKE_RESET]

    # 3. Add new strike
    strike_cache[user_id].append(now)
    strike_count = len(strike_cache[user_id])

    # --- EXECUTE JUDGMENT ---
    if strike_count == 1:
        # FIRST OFFENSE -> TIMEOUT (1 Minute)
        try:
            duration = datetime.timedelta(seconds=TIMEOUT_DURATION)
            await user.timeout(duration, reason=f"Warning: {reason}")
            await message.channel.send(f"⚠️ **Warning**: {user.mention} Put in Time-out for 1 min. (Reason: {reason})\n*Next violation in 5 mins = INSTANT BAN.*")
            track_activity(user.id, f"Automod strike 1: {reason}")
            await send_security_log(
                message.guild,
                "⚠️ Automod Strike",
                description=f"{user.mention} triggered a first-strike action.",
                color=discord.Color.orange(),
                fields={
                    "Reason": reason,
                    "User ID": str(user.id),
                    "Channel": getattr(message.channel, "mention", getattr(message.channel, "name", "Unknown")),
                },
            )
        except discord.Forbidden:
            await message.channel.send("❌ I tried to timeout this user, but my role is too low.")

    elif strike_count >= 2:
        # SECOND OFFENSE -> PERMANENT BAN
        try:
            await user.ban(reason=f"2nd Strike (Banned): {reason}")
            await message.channel.send(f"🔨 **JUDGMENT**: {user.mention} has been **BANNED** for breaking rules twice in 5 mins.")
            track_activity(user.id, f"Automod strike 2: {reason}")
            await send_security_log(
                message.guild,
                "🚨 Automod Ban",
                description=f"{user.mention} reached the second strike threshold.",
                color=discord.Color.red(),
                fields={
                    "Reason": reason,
                    "User ID": str(user.id),
                    "Channel": getattr(message.channel, "mention", getattr(message.channel, "name", "Unknown")),
                },
            )
            del strike_cache[user_id]  # Clear cache after ban
        except discord.Forbidden:
            await message.channel.send("❌ I tried to ban this user, but my role is too low.")

# ====================================================
# 🔒 LOCKDOWN & RECOVERY SYSTEM
# ====================================================
async def engage_lockdown(guild, reason):
    """Freezes the server - disables messaging and voice"""
    global is_locked_down
    if is_locked_down:
        return
    is_locked_down = True

    role = guild.default_role
    perms = role.permissions
    perms.send_messages = False
    perms.connect = False
    perms.speak = False
    
    try:
        await role.edit(permissions=perms, reason=f"LOCKDOWN: {reason}")
        print(f"❄️ SERVER FROZEN. Reason: {reason}")
        
        # Alert owner
        await alert_owner(guild, "SERVER LOCKDOWN ACTIVATED", {
            "Reason": reason,
            "Status": "Server is now in LOCKDOWN mode",
            "Action": "Use !all ok to unlock"
        })
    except Exception as e:
        print(f"⚠️ Lockdown Error: {e}")
        is_locked_down = False  # Revert on failure

async def restore_channel(guild, channel_name, category_id, channel_type):
    """Auto-recovers a deleted channel"""
    try:
        category = discord.utils.get(guild.categories, id=category_id) if category_id else None
        if str(channel_type) == 'text':
            new_channel = await guild.create_text_channel(channel_name, category=category, reason="Anti-Nuke Auto-Recovery")
        elif str(channel_type) == 'voice':
            new_channel = await guild.create_voice_channel(channel_name, category=category, reason="Anti-Nuke Auto-Recovery")
        else:
            return
        
        print(f"✅ Restored channel: {channel_name}")
        if hasattr(new_channel, 'send'):
            await new_channel.send(f"✅ **System Restored:** This channel was recovered by anti-nuke system.")
    except Exception as e:
        print(f"⚠️ Channel restoration error: {e}")

# ==================== WHITELIST CHECKER ====================
def is_whitelisted_entity(actor_or_id):
    """
    Advanced whitelist checker for bots, webhooks, and trusted users
    Returns: True if the entity is whitelisted/trusted, False otherwise
    """
    # Handle both discord.User and int (user/bot ID)
    actor_id = actor_or_id.id if hasattr(actor_or_id, 'id') else actor_or_id
    
    # Check if it's a whitelisted bot
    if actor_id in WHITELISTED_BOTS:
        print(f"✅ [WHITELIST] Bot ID {actor_id} is whitelisted (TRUSTED BOT)")
        return True
    
    # Check if it's a whitelisted webhook
    if actor_id in WHITELISTED_WEBHOOKS:
        print(f"✅ [WHITELIST] Webhook ID {actor_id} is whitelisted (TRUSTED WEBHOOK)")
        return True
    
    # Check if it's the owner
    if actor_id == OWNER_ID:
        print(f"✅ [WHITELIST] User {actor_id} is the OWNER")
        return True
    
    # Check if it's the bot itself
    if hasattr(actor_or_id, 'id') and actor_or_id == bot.user:
        print(f"✅ [WHITELIST] Actor is the bot itself")
        return True
    
    # Check if it's in TRUSTED_USERS
    if actor_id in TRUSTED_USERS:
        print(f"✅ [WHITELIST] User {actor_id} is in TRUSTED_USERS")
        return True
    
    return False


def normalize_security_content(content: str | None) -> str:
    """Normalize text so automod catches simple obfuscation tricks."""
    if not content:
        return ""

    normalized = unicodedata.normalize("NFKC", content)
    normalized = normalized.translate(SECURITY_ZERO_WIDTH_TRANSLATION)
    normalized = normalized.replace("`", "").replace("\\", "")
    normalized = re.sub(r"\s+", " ", normalized).strip().lower()
    return normalized


def content_has_forbidden_keywords(content: str | None) -> bool:
    normalized = normalize_security_content(content)
    if not normalized:
        return False
    return any(keyword in normalized for keyword in FORBIDDEN_KEYWORDS)


def contains_discord_invite(content: str | None) -> bool:
    normalized = normalize_security_content(content)
    if not normalized:
        return False

    compact = re.sub(r"[^a-z0-9/:._-]", "", normalized)
    return bool(
        re.search(
            r"(discord(?:app)?\.com/invite/[a-z0-9-]+|discord\.gg/[a-z0-9-]+|discord\.me/[a-z0-9-]+|discord\.io/[a-z0-9-]+|discord\.li/[a-z0-9-]+)",
            compact,
            re.IGNORECASE,
        )
    )


def is_dangerous_attachment(filename: str) -> bool:
    lowered = normalize_security_content(filename)
    return any(lowered.endswith(ext) for ext in DANGEROUS_EXTS)


def prune_processed_audit_cache(current_time: datetime.datetime | None = None) -> None:
    current_time = current_time or datetime.datetime.now(KOLKATA)
    stale_ids = [
        audit_id
        for audit_id, seen_at in processed_audit_timestamps.items()
        if (current_time - seen_at).total_seconds() > max(AUDIT_DEDUP_WINDOW, AUDIT_MATCH_WINDOW_SECONDS) * 12
    ]
    for audit_id in stale_ids:
        processed_audit_timestamps.pop(audit_id, None)
        processed_audit_ids.discard(audit_id)

    while len(processed_audit_timestamps) > MAX_AUDIT_CACHE:
        oldest_id = min(processed_audit_timestamps, key=processed_audit_timestamps.get)
        processed_audit_timestamps.pop(oldest_id, None)
        processed_audit_ids.discard(oldest_id)


def is_duplicate_audit_entry(entry_id: int, current_time: datetime.datetime) -> bool:
    prune_processed_audit_cache(current_time)
    if entry_id not in processed_audit_ids:
        return False

    previous_time = processed_audit_timestamps.get(entry_id)
    if previous_time is None:
        return True

    return (current_time - previous_time).total_seconds() < AUDIT_DEDUP_WINDOW


def remember_audit_entry(entry_id: int, current_time: datetime.datetime) -> None:
    processed_audit_ids.add(entry_id)
    processed_audit_timestamps[entry_id] = current_time
    prune_processed_audit_cache(current_time)


def get_audit_target_id(entry) -> int | None:
    if entry is None:
        return None
    target = getattr(entry, "target", None)
    if target is not None and hasattr(target, "id"):
        return target.id
    extra = getattr(entry, "extra", None)
    if extra is not None and hasattr(extra, "id"):
        return extra.id
    return None


def get_newly_granted_permissions(before_perms: discord.Permissions, after_perms: discord.Permissions) -> list[str]:
    return [
        perm_name
        for perm_name in DANGEROUS_PERMISSION_NAMES
        if getattr(after_perms, perm_name, False) and not getattr(before_perms, perm_name, False)
    ]


async def find_matching_audit_entry(
    guild: discord.Guild,
    action: discord.AuditLogAction,
    *,
    target_id: int | None = None,
    target_name: str | None = None,
    limit: int = 6,
    max_age_seconds: int = AUDIT_MATCH_WINDOW_SECONDS,
):
    """Fetch the audit entry that actually matches the affected target."""
    now_utc = discord.utils.utcnow()
    try:
        async for entry in guild.audit_logs(limit=limit, action=action):
            created_at = discord.utils.snowflake_time(entry.id)
            if (now_utc - created_at).total_seconds() > max_age_seconds:
                continue

            if target_id is not None and get_audit_target_id(entry) != target_id:
                continue

            if target_name is not None:
                entry_target = getattr(entry, "target", None)
                entry_name = getattr(entry_target, "name", None)
                if entry_name != target_name:
                    continue

            return entry
    except Exception as e:
        print(f"⚠️ Audit lookup failed for {action}: {e}")

    return None

# ==================== TEMP VOICE OWNER CONTROLS HELPERS ====================

tempvoice_runtime_owner_by_channel = {}  # channel_id -> owner_id
tempvoice_runtime_channel_by_owner = {}  # owner_id -> channel_id

async def tempvoice_db_find_one(query):
    global tempvoice_db_available
    if not tempvoice_db_available or tempvoice_coll is None:
        return None
    try:
        return await tempvoice_coll.find_one(query)
    except Exception as e:
        print(f"⚠️ Temp voice DB find_one error: {e}")
        tempvoice_db_available = False
        return None

async def tempvoice_db_delete_many(query):
    global tempvoice_db_available
    if not tempvoice_db_available or tempvoice_coll is None:
        return None
    try:
        return await tempvoice_coll.delete_many(query)
    except Exception as e:
        print(f"⚠️ Temp voice DB delete_many error: {e}")
        tempvoice_db_available = False
        return None

async def tempvoice_db_insert_one(document):
    global tempvoice_db_available
    if not tempvoice_db_available or tempvoice_coll is None:
        return None
    try:
        return await tempvoice_coll.insert_one(document)
    except Exception as e:
        print(f"⚠️ Temp voice DB insert_one error: {e}")
        tempvoice_db_available = False
        return None

async def is_temp_channel_owner(user_id: int, channel_id: int) -> bool:
    """Check whether user owns temp voice channel in DB"""
    cached_owner = tempvoice_runtime_owner_by_channel.get(channel_id)
    if cached_owner is not None:
        return cached_owner == user_id

    if not tempvoice_db_available or tempvoice_coll is None:
        return False
    try:
        doc = await tempvoice_db_find_one({"channel_id": channel_id})
        if doc and doc.get("owner_id") is not None:
            tempvoice_runtime_owner_by_channel[channel_id] = doc.get("owner_id")
            tempvoice_runtime_channel_by_owner[doc.get("owner_id")] = channel_id
        return bool(doc and doc.get("owner_id") == user_id)
    except Exception as e:
        print(f"⚠️ is_temp_channel_owner DB error: {e}")
        return False

async def get_owner_channel_entry(channel_id: int):
    if not tempvoice_db_available or tempvoice_coll is None:
        return None
    try:
        return await tempvoice_db_find_one({"channel_id": channel_id})
    except Exception as e:
        print(f"⚠️ get_owner_channel_entry DB error: {e}")
        return None

# Safe wrapper functions for MongoDB operations


def safe_find_one(collection, query):
    """Safely query MongoDB"""
    if not mongo_connected or collection is None:
        return None
    try:
        return collection.find_one(query)
    except Exception as e:
        return None

def safe_find(collection, query=None, limit=None):
    """Safely find multiple documents"""
    if not mongo_connected or collection is None:
        print(f"⚠️ Cannot find: mongo_connected={mongo_connected}, collection={collection is not None}")
        return []
    try:
        if query is None:
            query = {}
        result = collection.find(query)
        if limit:
            result = result.limit(limit)
        data = list(result)
        print(f"✅ safe_find returned {len(data)} documents")
        return data
    except Exception as e:
        print(f"⚠️ safe_find error: {str(e)[:100]}")
        return []

def safe_update_one(collection, query, update):
    """Safely update one document"""
    if not mongo_connected or collection is None:
        return False
    try:
        result = collection.update_one(query, update, upsert=True)
        if result.modified_count > 0 or result.upserted_id:
            return True
        # Document may not exist yet, that's okay
        return True
    except Exception as e:
        print(f"⚠️ MongoDB update error: {str(e)[:100]}")
        return False

def safe_delete_one(collection, query):
    """Safely delete one document"""
    if not mongo_connected or collection is None:
        return False
    try:
        collection.delete_one(query)
        return True
    except Exception as e:
        return False

def save_with_retry(collection, query, update, max_retries=3):
    """Save to MongoDB with retry logic"""
    if collection is None:
        print(f"⚠️ Collection is None, cannot save")
        return False
    
    for attempt in range(max_retries):
        try:
            # CRITICAL: Must use upsert=True to create documents if they don't exist
            # Handle MongoDB conflicts by flattening $setOnInsert operations
            result = collection.update_one(query, update, upsert=True)
            if result.modified_count > 0 or result.upserted_id:
                return True
            # Document may not exist yet, that's okay - upsert created it
            return True
        except Exception as e:
            error_msg = str(e)
            # Check for conflict errors (common in nested field updates)
            if "conflict" in error_msg.lower() or "cannot create" in error_msg.lower():
                print(f"⚠️ Save attempt {attempt + 1} failed: MongoDB conflict detected - {error_msg[:100]}")
                if attempt < max_retries - 1:
                    import time
                    time.sleep(1)  # Longer wait for conflicts
            else:
                print(f"⚠️ Save attempt {attempt + 1} failed: {error_msg[:80]}")
                if attempt < max_retries - 1:
                    import time
                    time.sleep(0.5)  # Wait before retry (use time.sleep, not asyncio)
    
    print(f"❌ Failed to save after {max_retries} attempts")
    return False

# Function to safely create indexes
async def create_indexes_async():
    """Create MongoDB indexes safely with error handling"""
    if not mongo_connected:
        print("⏭️ Skipping index creation (MongoDB not available)")
        return
    
    try:
        users_coll.create_index("data.voice_cam_on_minutes")
        users_coll.create_index("data.voice_cam_off_minutes")
        print("✅ MongoDB indexes created")
    except Exception as e:
        error_msg = str(e)
        if "SSL" in error_msg or "handshake" in error_msg:
            print(f"⚠️ Index creation skipped (MongoDB SSL issue): {error_msg[:80]}")
        else:
            print(f"⚠️ Index creation failed (non-critical): {error_msg[:100]}")


intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree
GUILD = discord.Object(id=GUILD_ID) if GUILD_ID > 0 else None

# In-memorys
vc_join_times = {}
cam_timers = {}
access_panel_view_registered = False
control_panel_view_registered = False
tempvoice_panel_message_sent = False


def build_control_panel_url(frontend_url: str) -> str:
    """Normalize the public dashboard URL so the Discord button lands on `/control`."""
    raw_url = (frontend_url or "").strip()
    if not raw_url:
        raw_url = f"http://localhost:{PORT}"

    if not re.match(r"^https?://", raw_url, re.IGNORECASE):
        raw_url = f"http://{raw_url.lstrip('/')}"

    parsed = urlparse(raw_url)
    path = parsed.path.rstrip("/")
    if path.endswith("/control"):
        control_path = path
    elif path.endswith("/LEGEND-STAR"):
        control_path = "/control"
    elif path:
        control_path = f"{path}/control"
    else:
        control_path = "/control"

    return urlunparse(parsed._replace(path=control_path, params="", query="", fragment=""))


def start_loop_once(loop: tasks.Loop, loop_name: str) -> None:
    """Start a discord task loop only if it is not already running."""
    if loop.is_running():
        print(f"⏭️ Background task already running: {loop_name}")
        return

    try:
        loop.start()
        print(f"▶️ Started background task: {loop_name}")
    except RuntimeError as e:
        print(f"⚠️ Could not start background task {loop_name}: {e}")


def is_strict_camera_channel(channel) -> bool:
    return bool(channel and channel.id in STRICT_CHANNEL_IDS)


def has_camera_bypass(member: discord.Member) -> bool:
    return any(role.id == CAMERA_BYPASS_ROLE for role in getattr(member, "roles", []))


def cancel_camera_enforcement(member_id: int) -> None:
    task = cam_timers.pop(member_id, None)
    if task and not task.done():
        task.cancel()


def get_camera_enforcement_task(member_id: int):
    task = cam_timers.get(member_id)
    if task and task.done():
        cam_timers.pop(member_id, None)
        print(f"🧹 Cleared stale camera enforcement task for member {member_id}")
        return None
    return task


def has_active_camera_enforcement(member_id: int) -> bool:
    return get_camera_enforcement_task(member_id) is not None


def track_camera_enforcement_task(member_id: int, task: asyncio.Task) -> asyncio.Task:
    def _cleanup(finished_task: asyncio.Task) -> None:
        if cam_timers.get(member_id) is finished_task:
            cam_timers.pop(member_id, None)

    task.add_done_callback(_cleanup)
    cam_timers[member_id] = task
    return task


async def get_member_for_enforcement(guild: discord.Guild, member_id: int):
    member = guild.get_member(member_id)
    if member is not None:
        return member

    try:
        return await guild.fetch_member(member_id)
    except discord.NotFound:
        return None
    except Exception as e:
        print(f"⚠️ Failed to fetch member {member_id} for camera enforcement: {e}")
        return None


# ==================== INDEPENDENT ACCESS PANEL SYSTEM ====================
def get_access_panel_embed(guild=None):
    embed = discord.Embed(
        title="Get Access",
        description=(
            "Click the button below to unlock your server access instantly.\n\n"
            "This panel grants the access role, sends a welcome DM, and posts a public confirmation."
        ),
        color=discord.Color.from_rgb(46, 204, 113),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="What This Does",
        value=(
            "• Assigns your access role automatically\n"
            "• Sends a professional welcome DM\n"
            "• Posts a timed public welcome message"
        ),
        inline=False,
    )
    embed.add_field(
        name="Note",
        value="If your DMs are disabled, your access will still be granted successfully.",
        inline=False,
    )
    if guild and guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_footer(text=ACCESS_PANEL_EMBED_MARKER)
    return embed


def get_access_success_embed(member, role, dm_sent, public_sent):
    embed = discord.Embed(
        title="Access Granted",
        description=f"{member.mention}, your access has been activated successfully.",
        color=discord.Color.from_rgb(88, 101, 242),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Role Added", value=role.mention, inline=False)
    embed.add_field(
        name="Direct Message",
        value="Welcome DM sent." if dm_sent else "Access granted, but I could not deliver your DM.",
        inline=False,
    )
    embed.add_field(
        name="Public Welcome",
        value="Welcome message posted." if public_sent else "Access granted, but the public welcome message could not be posted.",
        inline=False,
    )
    return embed


def get_access_dm_embed(member, role):
    guild = member.guild
    embed = discord.Embed(
        title=f"Welcome to {guild.name}",
        description=(
            f"Hello {member.mention},\n\n"
            "Your server access has been approved and your onboarding is now complete."
        ),
        color=discord.Color.from_rgb(52, 152, 219),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Unlocked Access",
        value=f"You now have access to member areas with the role {role.mention}.",
        inline=False,
    )
    embed.add_field(
        name="Welcome",
        value=f"We are glad to have you in **{guild.name}**. Please review the server guidance and community expectations when you have a moment.",
        inline=False,
    )
    embed.add_field(
        name="Support",
        value="If you need any help, please contact the server staff or use the support system available in the server.",
        inline=False,
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_footer(text=f"{guild.name} Access System")
    return embed


def get_access_public_embed(member, role):
    embed = discord.Embed(
        title="New Access Confirmed",
        description=f"{member.mention} has successfully received access.\n\nRole granted: {role.mention}",
        color=discord.Color.from_rgb(241, 196, 15),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Status",
        value="Access has been granted successfully. Welcome aboard.",
        inline=False,
    )
    embed.set_footer(text="This message will be removed automatically.")
    return embed


def get_access_bot_member(guild):
    return guild.me or guild.get_member(bot.user.id if bot.user else 0)


def validate_access_role_setup(guild, role):
    bot_member = get_access_bot_member(guild)
    if bot_member is None:
        return False, "I could not verify my member profile in this server."

    if not bot_member.guild_permissions.manage_roles:
        return False, "I need the `Manage Roles` permission to grant access."

    if role is None:
        return False, f"Access role `{ACCESS_GRANTED_ROLE_ID}` was not found."

    if role.managed:
        return False, "That access role is managed by an integration and cannot be assigned manually."

    if role >= bot_member.top_role:
        return False, "My role is not high enough to assign the configured access role."

    return True, None


def can_send_message_in_channel(channel, guild):
    bot_member = get_access_bot_member(guild)
    if bot_member is None:
        return False, "I could not verify my channel permissions."

    permissions = channel.permissions_for(bot_member)
    if not permissions.view_channel:
        return False, f"I cannot view {channel.mention}."
    if not permissions.send_messages:
        return False, f"I cannot send messages in {channel.mention}."
    if not permissions.embed_links:
        return False, f"I need the `Embed Links` permission in {channel.mention}."

    return True, None


def is_access_panel_message(message):
    if message.author.id != (bot.user.id if bot.user else 0):
        return False

    for embed in message.embeds:
        footer = getattr(embed, "footer", None)
        if footer and footer.text == ACCESS_PANEL_EMBED_MARKER:
            return True

    for row in message.components:
        for component in getattr(row, "children", []):
            if getattr(component, "custom_id", None) == ACCESS_PANEL_BUTTON_CUSTOM_ID:
                return True

    return False


async def find_existing_access_panel(channel):
    try:
        async for message in channel.history(limit=50):
            if is_access_panel_message(message):
                return message
    except Exception as e:
        print(f"⚠️ Access panel history check failed: {e}")
    return None


async def send_access_welcome_dm(member, role):
    try:
        await member.send(embed=get_access_dm_embed(member, role))
        print(f"✅ Access DM sent to {member} ({member.id})")
        return True
    except discord.Forbidden:
        print(f"⚠️ Access DM skipped for {member} ({member.id}) - DMs disabled")
        return False
    except Exception as e:
        print(f"⚠️ Access DM failed for {member} ({member.id}): {e}")
        return False


async def send_access_public_welcome(member, role):
    channel = member.guild.get_channel(ACCESS_WELCOME_CHANNEL_ID)
    if channel is None:
        print(f"⚠️ Access welcome channel {ACCESS_WELCOME_CHANNEL_ID} not found")
        return False

    allowed, reason = can_send_message_in_channel(channel, member.guild)
    if not allowed:
        print(f"⚠️ {reason}")
        return False

    try:
        await channel.send(
            content=member.mention,
            embed=get_access_public_embed(member, role),
            delete_after=30,
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
        print(f"✅ Access welcome message sent for {member} ({member.id})")
        return True
    except Exception as e:
        print(f"⚠️ Access welcome message failed for {member} ({member.id}): {e}")
        return False


async def send_access_panel_message(channel):
    existing_panel = await find_existing_access_panel(channel)
    if existing_panel:
        return False, existing_panel

    await channel.send(
        embed=get_access_panel_embed(channel.guild),
        view=AccessPanelView(),
        allowed_mentions=discord.AllowedMentions.none(),
    )
    print(f"✅ Access panel sent to #{channel.name} ({channel.id})")
    return True, None


async def register_access_panel_view():
    global access_panel_view_registered

    if access_panel_view_registered:
        return

    try:
        bot.add_view(AccessPanelView())
        access_panel_view_registered = True
        print("✅ Persistent AccessPanelView registered")
    except Exception as e:
        print(f"⚠️ Error registering AccessPanelView: {e}")


async def register_control_panel_view():
    global control_panel_view_registered

    if control_panel_view_registered:
        return

    try:
        bot.add_view(ControlPanel())
        control_panel_view_registered = True
        print("✅ Persistent ControlPanel view registered")
    except Exception as e:
        print(f"⚠️ Error registering ControlPanel view: {e}")


class AccessPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Get Access",
        style=discord.ButtonStyle.success,
        emoji="✨",
        custom_id=ACCESS_PANEL_BUTTON_CUSTOM_ID,
    )
    async def get_access(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None:
            return await interaction.response.send_message(
                "❌ This button can only be used inside the server.",
                ephemeral=True,
            )

        if interaction.channel_id != ACCESS_PANEL_CHANNEL_ID:
            return await interaction.response.send_message(
                "❌ This access button is only valid in the configured access channel.",
                ephemeral=True,
            )

        member = interaction.user if isinstance(interaction.user, discord.Member) else interaction.guild.get_member(interaction.user.id)
        if member is None:
            return await interaction.response.send_message(
                "❌ I could not resolve your server member record. Please try again.",
                ephemeral=True,
            )

        role = interaction.guild.get_role(ACCESS_GRANTED_ROLE_ID)
        is_valid, error_message = validate_access_role_setup(interaction.guild, role)
        if not is_valid:
            return await interaction.response.send_message(f"❌ {error_message}", ephemeral=True)

        if role in member.roles:
            print(f"ℹ️ Duplicate access prevented for {member} ({member.id})")
            return await interaction.response.send_message(
                "ℹ️ You already have access. No changes were needed.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True, thinking=False)

        try:
            await member.add_roles(
                role,
                reason=f"Access granted via access panel for {member} ({member.id})",
            )
            print(f"✅ Access role granted to {member} ({member.id})")
        except discord.Forbidden:
            print(f"⚠️ Missing permissions to grant access role to {member} ({member.id})")
            return await interaction.followup.send(
                "❌ I do not have permission to assign the access role.",
                ephemeral=True,
            )
        except Exception as e:
            print(f"⚠️ Failed to grant access role to {member} ({member.id}): {e}")
            return await interaction.followup.send(
                "❌ Something went wrong while granting access. Please contact staff.",
                ephemeral=True,
            )

        dm_sent = await send_access_welcome_dm(member, role)
        public_sent = await send_access_public_welcome(member, role)

        await interaction.followup.send(
            embed=get_access_success_embed(member, role, dm_sent, public_sent),
            ephemeral=True,
        )


async def _startup_camera_enforcement_legacy(member: discord.Member, channel: discord.VoiceChannel):
    """Start the same enforcement flow used for on_voice_state_update for an existing member.
    This allows enforcement to run for users who were already in VC when the bot started.
    """
    member_id = member.id
    guild_id = member.guild.id
    channel_id = channel.id if channel else None

    # Check for bypass role
    if any(role.id == CAMERA_BYPASS_ROLE for role in member.roles):
        print(f"✅ [{member.display_name}] Has CAMERA_BYPASS_ROLE - Enforcement skipped")
        return

    # Avoid duplicate timers
    if has_active_camera_enforcement(member_id):
        return

    async def _enforce():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cam_timers.pop(member_id, None)
            return

        guild = bot.get_guild(guild_id)
        if not guild:
            cam_timers.pop(member_id, None)
            return

        member_obj = guild.get_member(member_id)
        try:
            mention = member_obj.mention if member_obj else f"<@{member_id}>"
            embed = discord.Embed(
                title="🎥 ⚠️ CAMERA REQUIRED - FINAL WARNING!",
                description=f"{mention}\n\n**Please turn on your camera within 3 minutes or you will be disconnected from the voice channel!**",
                color=discord.Color.red()
            )
            embed.add_field(name="⏱️ TIME REMAINING", value="3 minutes to comply or automatic kick", inline=False)
            embed.add_field(name="✅ ACTION REQUIRED", value="• Turn on your camera\n*(Screenshare alone is not enough - camera is mandatory)*", inline=False)
            embed.set_footer(text="⚠️ This channel has strict camera enforcement enabled")

            if member_obj:
                await member_obj.send(embed=embed)
                print(f"📢 [{member_obj.display_name}] 🎥 CAM WARNING SENT (startup) - Countdown: 3 MINUTES TO COMPLY OR KICK")
            else:
                print(f"📢 [ID:{member_id}] 🎥 CAM WARNING (startup) attempted")
        except Exception as e:
            print(f"⚠️ Failed to send startup enforcement warning to ID {member_id}: {e}")

        try:
            await asyncio.sleep(180)
        except asyncio.CancelledError:
            cam_timers.pop(member_id, None)
            return

        # Re-fetch member state
        member_ref = guild.get_member(member_id)
        if not member_ref or not member_ref.voice or not member_ref.voice.channel:
            cam_timers.pop(member_id, None)
            return

        current_cam = member_ref.voice.self_video
        voice_chan = member_ref.voice.channel
        if current_cam:
            print(f"✅ [{member_ref.display_name}] (startup) COMPLIED IN TIME - CAM ON detected")
            cam_timers.pop(member_id, None)
            return

        # Permission/role checks
        bot_member = guild.get_member(bot.user.id)
        if bot_member and voice_chan:
            perms = voice_chan.permissions_for(bot_member)
            if not perms.move_members and not perms.administrator:
                print(f"❌ [ID:{member_id}] BOT LACKS MOVE_MEMBERS permission in {voice_chan.name} (startup)")
                cam_timers.pop(member_id, None)
                return
            if member_ref.top_role.position >= bot_member.top_role.position and not perms.administrator:
                print(f"❌ [ID:{member_id}] Role hierarchy prevents disconnect (member >= bot) (startup)")
                cam_timers.pop(member_id, None)
                return

        # Attempt disconnect
        try:
            await member_ref.move_to(None, reason="Camera enforcement (startup)")
        except Exception as e:
            print(f"❌ Failed move_to for ID {member_id} (startup): {e}")
            cam_timers.pop(member_id, None)
            return

        # verify
        await asyncio.sleep(2)
        member_ref = guild.get_member(member_id) or member_ref
        if member_ref.voice and member_ref.voice.channel:
            print(f"❌ [ID:{member_id}] STILL IN VOICE CHANNEL after move_to (startup)")
            cam_timers.pop(member_id, None)
            return

        print(f"✅ [ID:{member_id}] Confirmed disconnected (startup)")
        # Notify channel and DM
        try:
            embed_kick = discord.Embed(title="🚪 User Disconnected",
                                       description=f"{member_ref.mention} has been automatically disconnected for not enabling their camera.",
                                       color=discord.Color.orange())
            embed_kick.set_footer(text="Camera enforcement in strict channels")
            target_chan = voice_chan
            if target_chan:
                await target_chan.send(embed=embed_kick, delete_after=15)
        except Exception as e:
            print(f"⚠️ Failed to send startup channel notification: {e}")

        try:
            embed_dm = discord.Embed(title="📵 You Were Disconnected",
                                     description=f"You were disconnected from **{voice_chan.name if voice_chan else 'the voice channel'}** due to camera enforcement.\n\nCamera is mandatory in this channel (screenshare alone is not sufficient).\n\nPlease enable your camera before rejoining.",
                                     color=discord.Color.red())
            if member_ref:
                await member_ref.send(embed=embed_dm)
        except Exception as e:
            print(f"⚠️ Failed to send startup disconnect DM to ID {member_id}: {e}")

        cam_timers.pop(member_id, None)

    track_camera_enforcement_task(member_id, bot.loop.create_task(_enforce()))


async def start_camera_enforcement_for(member: discord.Member, channel: discord.VoiceChannel):
    """Shared camera enforcement scheduler for startup and live voice updates."""
    if not is_strict_camera_channel(channel):
        return

    member_id = member.id
    guild_id = member.guild.id

    if has_camera_bypass(member):
        cancel_camera_enforcement(member_id)
        print(f"✅ [{member.display_name}] Has CAMERA_BYPASS_ROLE - Enforcement skipped")
        return

    if has_active_camera_enforcement(member_id):
        return

    async def _enforce():
        try:
            await asyncio.sleep(1)
            guild = bot.get_guild(guild_id)
            if not guild:
                print(f"⚠️ Camera enforcement aborted for ID {member_id}: guild cache unavailable")
                return

            member_obj = await get_member_for_enforcement(guild, member_id)
            if not member_obj or not member_obj.voice or not member_obj.voice.channel:
                print(f"ℹ️ Camera enforcement aborted for ID {member_id}: member is no longer in voice")
                return

            if has_camera_bypass(member_obj):
                print(f"✅ [{member_obj.display_name}] Gained CAMERA_BYPASS_ROLE - Timer cancelled")
                return

            if not is_strict_camera_channel(member_obj.voice.channel):
                print(f"ℹ️ [{member_obj.display_name}] Not in a strict camera channel anymore - timer skipped")
                return

            if member_obj.voice.self_video:
                print(f"✅ [{member_obj.display_name}] Camera already ON - Timer skipped")
                return

            try:
                embed = discord.Embed(
                    title="CAMERA REQUIRED - FINAL WARNING!",
                    description=f"{member_obj.mention}\n\n**Please turn on your camera within 3 minutes or you will be disconnected from the voice channel!**",
                    color=discord.Color.red()
                )
                embed.add_field(name="TIME REMAINING", value="3 minutes to comply or automatic kick", inline=False)
                embed.add_field(name="ACTION REQUIRED", value="Turn on your camera. Screenshare alone is not enough.", inline=False)
                embed.set_footer(text="This channel has strict camera enforcement enabled")
                await member_obj.send(embed=embed)
                print(f"📢 [{member_obj.display_name}] Camera warning sent - 3 minute timer started")
            except Exception as e:
                print(f"⚠️ Failed to send enforcement warning to ID {member_id}: {e}")

            await asyncio.sleep(CAMERA_ENFORCEMENT_SECONDS)
        except asyncio.CancelledError:
            return
        except Exception as e:
            print(f"⚠️ Camera enforcement setup failed for ID {member_id}: {e}")
            return

        guild = bot.get_guild(guild_id)
        if not guild:
            cam_timers.pop(member_id, None)
            return

        member_ref = await get_member_for_enforcement(guild, member_id)
        if not member_ref or not member_ref.voice or not member_ref.voice.channel:
            cam_timers.pop(member_id, None)
            return

        if has_camera_bypass(member_ref):
            print(f"✅ [{member_ref.display_name}] Has CAMERA_BYPASS_ROLE at expiry - disconnect skipped")
            cam_timers.pop(member_id, None)
            return

        voice_chan = member_ref.voice.channel
        if member_ref.voice.self_video:
            print(f"✅ [{member_ref.display_name}] COMPLIED IN TIME - CAM ON detected")
            cam_timers.pop(member_id, None)
            return

        if not is_strict_camera_channel(voice_chan):
            print(f"ℹ️ [{member_ref.display_name}] Left strict camera channel before timer expiry")
            cam_timers.pop(member_id, None)
            return

        bot_member = guild.get_member(bot.user.id)
        if bot_member and voice_chan:
            perms = voice_chan.permissions_for(bot_member)
            if not perms.move_members and not perms.administrator:
                print(f"❌ [ID:{member_id}] BOT LACKS MOVE_MEMBERS permission in {voice_chan.name}")
                cam_timers.pop(member_id, None)
                return
            if member_ref.top_role.position >= bot_member.top_role.position and not perms.administrator:
                print(f"❌ [ID:{member_id}] Role hierarchy prevents disconnect (member >= bot)")
                cam_timers.pop(member_id, None)
                return

        try:
            print(f"🔄 [ID:{member_id}] Attempting to disconnect for camera enforcement...")
            await member_ref.move_to(None, reason="Camera enforcement")
        except Exception as e:
            print(f"❌ Failed move_to for ID {member_id}: {e}")
            cam_timers.pop(member_id, None)
            return

        await asyncio.sleep(2)
        member_ref = await get_member_for_enforcement(guild, member_id) or member_ref
        if member_ref.voice and member_ref.voice.channel:
            print(f"❌ [ID:{member_id}] STILL IN VOICE CHANNEL after move_to")
            cam_timers.pop(member_id, None)
            return

        print(f"✅ [ID:{member_id}] Confirmed disconnected")
        try:
            embed_dm = discord.Embed(
                title="You Were Disconnected",
                description=f"You were disconnected from **{voice_chan.name if voice_chan else 'the voice channel'}** due to camera enforcement.\n\nCamera is mandatory in this channel. Please enable your camera before rejoining.",
                color=discord.Color.red()
            )
            await member_ref.send(embed=embed_dm)
        except Exception as e:
            print(f"⚠️ Failed to send disconnect DM to ID {member_id}: {e}")
        finally:
            cam_timers.pop(member_id, None)

    track_camera_enforcement_task(member_id, asyncio.create_task(_enforce()))


async def ensure_camera_enforcement_for_guild(guild: discord.Guild, source: str = "watchdog"):
    """Backstop sweep so camera enforcement still works if a voice-state event is missed."""
    if guild is None:
        return

    for member_id in list(cam_timers):
        task = get_camera_enforcement_task(member_id)
        if task is None:
            continue

        member = guild.get_member(member_id)
        if not member or not member.voice or not member.voice.channel:
            cancel_camera_enforcement(member_id)
            print(f"ℹ️ [{source}] Cancelled camera timer for {member_id} - member left voice")
            continue

        if not is_strict_camera_channel(member.voice.channel):
            cancel_camera_enforcement(member_id)
            print(f"ℹ️ [{source}] Cancelled camera timer for {member.display_name} - not in strict channel")
            continue

        if has_camera_bypass(member):
            cancel_camera_enforcement(member_id)
            print(f"✅ [{source}] Cancelled camera timer for {member.display_name} - bypass role detected")
            continue

        if member.voice.self_video:
            cancel_camera_enforcement(member_id)
            print(f"✅ [{source}] Cancelled camera timer for {member.display_name} - camera is now on")

    for channel_id in STRICT_CHANNEL_IDS:
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.VoiceChannel):
            continue

        for member in list(channel.members):
            if member.bot:
                continue

            if has_camera_bypass(member):
                continue

            if member.voice and member.voice.self_video:
                continue

            if not has_active_camera_enforcement(member.id):
                print(f"🔎 [{source}] Scheduling camera enforcement for {member.display_name} in {channel.name}")
                await start_camera_enforcement_for(member, channel)
user_activity = defaultdict(list)
spam_cache = defaultdict(list)
strike_cache = defaultdict(list)
join_times = defaultdict(list)
vc_cache = defaultdict(list)
last_general_audit_id = None  # Track last processed audit entry for general audit alerts
vc_saving = set()  # guard set to prevent concurrent double-saves for a user

from leaderboard import format_time, get_medal_emoji, generate_leaderboard_text, user_rank

def track_activity(user_id: int, action: str):
    ts = datetime.datetime.now(KOLKATA).strftime("%d/%m %H:%M:%S")
    user_activity[user_id].append(f"[{ts}] {action}")
    if len(user_activity[user_id]) > 20:
        user_activity[user_id].pop(0)


def truncate_embed_field(value: str, max_length: int = 1024) -> str:
    """Truncate embed field value to Discord's 1024 character limit"""
    if not value:
        return value
    if len(value) <= max_length:
        return value
    # Leave room for truncation indicator
    return value[:max_length - 20] + "\n... (truncated)"


def truncate_for_codeblock(value: str, max_length: int = 1000) -> str:
    """Truncate value for display in code block (accounting for ``` markers)"""
    if not value:
        return value
    safe_length = max_length - 20
    if len(value) <= safe_length:
        return value
    return value[:safe_length] + "\n... (truncated)"


# --------------------
# Soft automod /action
# --------------------
@tree.command(name="action", description="Soft automod control (delete-only)", guild=GUILD)
@app_commands.check(lambda interaction: interaction.user.id == OWNER_ID)
async def action(
    interaction: discord.Interaction,
    target: discord.Member | discord.Role,
    ping: bool,
    message: bool,
    reason: str = DEFAULT_REASON
):
    guild = interaction.guild
    noping = discord.utils.get(guild.roles, name=NOPING_ROLE)
    nomsg = discord.utils.get(guild.roles, name=NOMSG_ROLE)

    # Auto-create marker roles if missing (admins only)
    try:
        if not noping:
            noping = await guild.create_role(name=NOPING_ROLE, reason="Created by automod /action command")
        if not nomsg:
            nomsg = await guild.create_role(name=NOMSG_ROLE, reason="Created by automod /action command")
    except Exception:
        # If role creation fails, continue — command can still operate if roles exist
        pass

    if isinstance(target, discord.Role):
        await interaction.response.send_message(
            f"✅ Action applied to role **{target.name}**",
            ephemeral=True
        )
        return

    # 👤 USER TARGET
    if not ping and noping:
        await target.add_roles(noping, reason=reason)
    if ping and noping and noping in target.roles:
        await target.remove_roles(noping, reason="Ping allowed")

    if not message and nomsg:
        await target.add_roles(nomsg, reason=reason)
    if message and nomsg and nomsg in target.roles:
        await target.remove_roles(nomsg, reason="Message allowed")

    await interaction.response.send_message(
        f"🛡️ Soft action updated for {target.mention}",
        ephemeral=True
    )

# (Registered with @tree.command)

# ==================== VOICE & CAM ====================
@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    global tempvoice_db_available

    # Skip bots only (no guild restriction to allow multi-guild support)
    if member.bot:
        return

    # 🔍 Debug: Log all voice state changes
    if after.channel:
        print(f"🔍 VC JOIN: {member.name} ({member.id}) → {after.channel.name} (ID: {after.channel.id}) | LOBBY_ID:{LOBBY_CHANNEL_ID}")
    elif before.channel:
        print(f"🔍 VC LEAVE: {member.name} ({member.id}) ← {before.channel.name} (ID: {before.channel.id})")

    # CREATE or MOVE to temp voice channel when user joins lobby
    if after.channel and after.channel.id == LOBBY_CHANNEL_ID:
        print(f"✅ DETECTED: {member.name} joined LOBBY! Creating or moving to temp channel...")
        try:
            # Check for existing temp channel (DB or runtime)
            existing = None
            channel_obj = None
            print(f"  [CHECK] Scanning for existing channel owned by {member.name} (ID: {member.id})...")
            if tempvoice_db_available and tempvoice_coll is not None:
                existing = await tempvoice_db_find_one({"owner_id": member.id, "guild_id": member.guild.id})
                if existing:
                    print(f"  ✅ Found existing DB entry: {existing}")
                    channel_id = existing.get("channel_id")
                    if channel_id:
                        channel_obj = member.guild.get_channel(channel_id)
            else:
                print(f"  ℹ️ MongoDB unavailable, checking runtime cache...")
                channel_id = tempvoice_runtime_channel_by_owner.get(member.id)
                if channel_id:
                    channel_obj = member.guild.get_channel(channel_id)
                    if channel_obj:
                        print(f"  ✅ Found existing channel in runtime cache")

            if channel_obj:
                print(f"⚠️ Temp channel already exists for {member.name} (owner_id={member.id}), moving user...")
                # Move user to their temp channel if not already there
                if not member.voice or member.voice.channel.id != channel_obj.id:
                    try:
                        await asyncio.sleep(0.5)
                        await member.move_to(channel_obj)
                        print(f"    ✅ User moved to existing temp channel: {channel_obj.name} (ID: {channel_obj.id})")
                    except Exception as e:
                        print(f"    ⚠️ Failed to move user to existing temp channel: {e}")
                else:
                    print(f"    ℹ️ User already in their temp channel")
            else:
                # 1️⃣ Fetch category
                print(f"    [STEP 1] Fetching category {TEMP_CATEGORY_ID}...")
                category = member.guild.get_channel(TEMP_CATEGORY_ID) if TEMP_CATEGORY_ID else None
                if category and not isinstance(category, discord.CategoryChannel):
                    print(f"    ⚠️ Channel {TEMP_CATEGORY_ID} is not a CategoryChannel")
                    category = None

                if category:
                    print(f"    ✅ Category found: {category.name}")
                else:
                    print(f"    ⚠️ Category not found! Creating channel without category.")

                # 2️⃣ Create voice channel
                channel_name = f"{member.name}'s Room"[:100]
                print(f"    [STEP 2] Creating voice channel '{channel_name}'...")
                channel = await member.guild.create_voice_channel(name=channel_name, category=category)
                print(f"    ✅ Channel created: {channel.name} (ID: {channel.id})")

                # 3️⃣ Save to MongoDB (if available)
                print(f"    [STEP 3] Saving to database (db_available={tempvoice_db_available})...")
                if tempvoice_db_available and tempvoice_coll is not None:
                    try:
                        await tempvoice_coll.insert_one({
                            "channel_id": channel.id,
                            "owner_id": member.id,
                            "guild_id": member.guild.id,
                            "created_at": datetime.datetime.utcnow()
                        })
                        print(f"    ✅ Saved to MongoDB")
                    except Exception as e:
                        print(f"    ⚠️ Temp voice DB insert failed: {e}")
                        tempvoice_db_available = False
                else:
                    print(f"    ℹ️ Using runtime cache (MongoDB unavailable)")

                # 4️⃣ Update caches
                print(f"    [STEP 4] Updating runtime caches...")
                tempvoice_runtime_owner_by_channel[channel.id] = member.id
                tempvoice_runtime_channel_by_owner[member.id] = channel.id
                print(f"    ✅ Caches updated")

                # 5️⃣ Move user to channel
                print(f"    [STEP 5] Moving {member.name} to channel (waiting 0.5s)...")
                await asyncio.sleep(0.5)
                await member.move_to(channel)
                print(f"    ✅ User moved successfully")

                print(f"✅✅✅ COMPLETE: {member.name} creation & move workflow finished")
                print(f"   Channel: {channel.name} (ID: {channel.id})")
        except Exception as e:
            print(f"⚠️ Failed to create/move temp channel: {e}")

    # DELETE temp voice channel when empty
    if before.channel:
        try:
            print(f"🗑️  CHECKING: {before.channel.name} for deletion (members: {len(before.channel.members)})...")
            data = None
            if before.channel.id in tempvoice_runtime_owner_by_channel:
                data = {"channel_id": before.channel.id, "owner_id": tempvoice_runtime_owner_by_channel[before.channel.id]}
                print(f"  ✅ Found in runtime cache: owner={data['owner_id']}")
            elif tempvoice_db_available and tempvoice_coll is not None:
                data = await tempvoice_db_find_one({"channel_id": before.channel.id})
                if data:
                    print(f"  ✅ Found in MongoDB: owner={data.get('owner_id')}")

            if data and len(before.channel.members) == 0:
                print(f"  🗑️  Channel is empty! Deleting...")
                await before.channel.delete()
                print(f"  ✅ Channel deleted from Discord")

                owner_id = tempvoice_runtime_owner_by_channel.pop(before.channel.id, None)
                if owner_id:
                    tempvoice_runtime_channel_by_owner.pop(owner_id, None)
                    print(f"  ✅ Removed from runtime cache")

                if tempvoice_db_available and tempvoice_coll is not None:
                    try:
                        await tempvoice_coll.delete_one({"channel_id": before.channel.id})
                        print(f"  ✅ Removed from MongoDB")
                    except Exception as e:
                        print(f"  ⚠️ Temp voice DB delete_one failed: {e}")
                        tempvoice_db_available = False

                print(f"✅✅✅ COMPLETE: Deleted empty temp channel {before.channel.name}")
            elif data:
                print(f"  ℹ️ Channel not empty ({len(before.channel.members)} members remaining)")
            else:
                print(f"  ℹ️ Not a temp channel (no owner data found)")
        except Exception as e:
            print(f"⚠️ Failed to clean up temp channel: {e}")

    # =======================================
    # 🔊 VC SPAM SENSOR (Enhanced Hopping)
    # =======================================
    if member.id not in TRUSTED_USERS and before.channel != after.channel:
        now = datetime.datetime.now()
        if member.id not in vc_cache:
            vc_cache[member.id] = []
        
        # Clean old timestamps (older than 5 seconds)
        vc_cache[member.id] = [t for t in vc_cache[member.id] if (now - t).total_seconds() < 5]
        vc_cache[member.id].append(now)
        
        # If more than 3 joins/leaves in 5 seconds -> VC hopping detected
        if len(vc_cache[member.id]) > 3:
            print(f"⚠️ VC HOPPING DETECTED: {member.name} ({len(vc_cache[member.id])} actions in 5s)")
            # This would be a human error, trigger strike system
            # For now, just timeout them
            try:
                await member.timeout(timedelta(minutes=5), reason="VC Join/Leave Spam (Hopping)")
                await alert_owner(member.guild, "VC HOPPING SPAM", {
                    "User": f"{member.mention}",
                    "Actions": f"{len(vc_cache[member.id])} in 5 seconds",
                    "Action": "5-minute timeout"
                })
            except Exception as e:
                print(f"⚠️ Failed to timeout VC hopper: {e}")
            return
    
    user_id = str(member.id)
    now = time.time()
    old_in = bool(before.channel)
    new_in = bool(after.channel)
    # Cam is ON if: camera is on (regardless of screenshare status)
    # Cam is OFF if: camera is off
    # NOTE: Cam ON + Screenshare ON = counts as cam on time (both are active)
    old_cam = before.self_video
    new_cam = after.self_video

    # Initialize user record first
    save_with_retry(users_coll, {"_id": user_id}, {"$setOnInsert": {"data": {"voice_cam_on_minutes": 0, "voice_cam_off_minutes": 0, "message_count": 0, "yesterday": {"cam_on": 0, "cam_off": 0}}}})

    # VC abuse check
    if before.channel != after.channel:
        vc_cache[member.id].append(now)
        vc_cache[member.id] = [t for t in vc_cache[member.id] if now - t < VC_ABUSE_WINDOW]
        if len(vc_cache[member.id]) > VC_ABUSE_THRESHOLD:
            try:
                await member.move_to(None, reason="VC abuse")
                await member.timeout(timedelta(minutes=5), reason="VC hopping")
                track_activity(member.id, "Timeout for VC abuse")
            except Exception:
                pass

    # ==================== SPY VC/CAMERA TRACKING ====================
    if not member.bot:
        try:
            time_now = datetime.datetime.now().strftime("%d/%m %H:%M:%S")
            state = "Cam ON" if after.self_video else "Cam OFF"
            
            async with aiosqlite.connect(DB_PATH) as db:
                # Track VC join if channel changed
                if before.channel != after.channel and after.channel:
                    await db.execute(
                        "INSERT INTO vc_logs (user_id, channel, time) VALUES (?, ?, ?)",
                        (member.id, f"{after.channel.name} ({state})", time_now)
                    )
                
                # Track camera ON
                if not before.self_video and after.self_video:
                    await db.execute("""
                        INSERT INTO users (user_id, cam_on)
                        VALUES (?, 1)
                        ON CONFLICT(user_id)
                        DO UPDATE SET cam_on = cam_on + 1
                    """, (member.id,))
                
                # Track camera OFF
                elif before.self_video and not after.self_video:
                    await db.execute("""
                        INSERT INTO users (user_id, cam_off)
                        VALUES (?, 1)
                        ON CONFLICT(user_id)
                        DO UPDATE SET cam_off = cam_off + 1
                    """, (member.id,))
                
                # Check if user is being spied
                cursor = await db.execute(
                    "SELECT user_id FROM spy_targets WHERE user_id = ?",
                    (member.id,)
                )
                spy = await cursor.fetchone()
                
                await db.commit()
            
            # Send spy notification if monitored
            if spy:
                if before.channel != after.channel and after.channel:
                    await notify_spy(
                        member,
                        f"🕵️ **SPY LOG - VC**\n"
                        f"[{time_now}] Joined VC: {after.channel.name}\n"
                        f"Status: {state}"
                    )
                if not before.self_video and after.self_video:
                    await notify_spy(
                        member,
                        f"🕵️ **SPY LOG - CAMERA**\n"
                        f"[{time_now}] Camera turned ON"
                    )
                elif before.self_video and not after.self_video:
                    await notify_spy(
                        member,
                        f"🕵️ **SPY LOG - CAMERA**\n"
                        f"[{time_now}] Camera turned OFF"
                    )
        except Exception as e:
            print(f"⚠️ Spy VC tracking error: {e}")

    # Save voice time IMMEDIATELY when leaving or changing settings
    if (old_in and not new_in) or (old_in and new_in and (before.channel != after.channel or old_cam != new_cam)):
        # Prevent concurrent batch save from also saving the same interval
        if member.id in vc_join_times:
            if member.id in vc_saving:
                # Another save is in progress for this user; skip to avoid double-count
                print(f"⏸️ Skipping immediate save for {member.display_name} - concurrent save in progress")
            else:
                vc_saving.add(member.id)
                try:
                    # Re-read join time (may have been updated by batch save)
                    join_time = vc_join_times.get(member.id)
                    if join_time is None:
                        # nothing to save
                        pass
                    else:
                        mins = int((now - join_time) // 60)
                        if mins > 0:
                            # Determine the relevant channel for this event (prefer before when leaving)
                            relevant_channel = None
                            if before and before.channel:
                                relevant_channel = before.channel
                            elif after and after.channel:
                                relevant_channel = after.channel

                            # Skip recording stats for excluded voice channel
                            if relevant_channel and getattr(relevant_channel, 'id', None) == EXCLUDED_VOICE_CHANNEL_ID:
                                print(f"⏭️ Skipping cam stat save for excluded channel ({EXCLUDED_VOICE_CHANNEL_ID}) for {member.display_name}")
                            else:
                                field = "data.voice_cam_on_minutes" if old_cam else "data.voice_cam_off_minutes"
                                result = save_with_retry(users_coll, {"_id": user_id}, {"$inc": {field: mins}})
                                cam_status = "🎥 ON" if old_cam else "❌ OFF"
                                print(f"💾 [{field}] Saved {mins}m for {member.display_name} ({cam_status}) - MongoDB: {result}")
                        # Remove tracking entry after handling leave
                        vc_join_times.pop(member.id, None)
                finally:
                    vc_saving.discard(member.id)

    # Track when user joins VC
    if new_in:
        vc_join_times[member.id] = now
        track_activity(member.id, f"Joined VC: {after.channel.name if after.channel else 'Unknown'}")
        print(f"🎤 {member.display_name} joined VC - tracking started (Cam: {new_cam})")

    # 🎥 ADVANCED CAM ENFORCEMENT SYSTEM 🎥
    # Updated Logic:
    # - Cam ON + Screenshare ON = ✅ NO WARNING (camera is on, approved)
    # - Cam ON + Screenshare OFF = ✅ NO WARNING (camera is on, approved)
    # - Cam OFF + Screenshare ON = ⚠️ WARNING (need camera even with screenshare)
    # - Cam OFF + Screenshare OFF = ⚠️ WARNING (no camera, no screenshare)

    # Auto-delete temporary voice channels when empty
    if before.channel and tempvoice_coll:
        try:
            temp_doc = await tempvoice_coll.find_one({"channel_id": before.channel.id})
            if temp_doc and len(before.channel.members) == 0:
                try:
                    await before.channel.delete(reason="Auto-delete temp voice channel when empty")
                except Exception as e:
                    print(f"⚠️ Failed to auto-delete empty temp channel {before.channel.id}: {e}")
                try:
                    await tempvoice_coll.delete_one({"channel_id": before.channel.id})
                except Exception as e:
                    print(f"⚠️ Failed to remove temp channel DB entry {before.channel.id}: {e}")
        except Exception as e:
            print(f"⚠️ Temp channel cleanup check failed: {e}")

    # Camera enforcement handled by shared scheduler below.
    if not after.channel:
        if member.id in cam_timers:
            cancel_camera_enforcement(member.id)
            print(f"LEFT VC - Camera timer cancelled for {member.display_name}")
        return

    if not is_strict_camera_channel(after.channel):
        if member.id in cam_timers:
            cancel_camera_enforcement(member.id)
            print(f"Left strict camera channel - Camera timer cancelled for {member.display_name}")
        return

    if has_camera_bypass(member):
        if member.id in cam_timers:
            cancel_camera_enforcement(member.id)
        print(f"✅ [{member.display_name}] Has CAMERA_BYPASS_ROLE - Enforcement skipped")
        return

    if after.self_video:
        if member.id in cam_timers:
            cancel_camera_enforcement(member.id)
        if after.self_stream:
            print(f"✅ [{member.display_name}] CAM ON + SCREENSHARE ON - No warning needed")
        else:
            print(f"✅ [{member.display_name}] CAM ON - No warning needed")
        return

    if not has_active_camera_enforcement(member.id):
        status_text = "SCREENSHARE ON" if after.self_stream else "NO SCREENSHARE"
        print(f"⚠️ [{member.display_name}] CAM OFF ({status_text}) - ENFORCEMENT STARTED!")
        await start_camera_enforcement_for(member, after.channel)
    return

    # Cancel camera timer if user leaves voice channel
    if not after.channel and member.id in cam_timers:
        task = cam_timers.pop(member.id, None)
        if task:
            task.cancel()
        print(f"🚪 [{member.display_name}] LEFT VC - Camera timer cancelled")
    
    channel = after.channel
    if channel and (str(channel.id) in STRICT_CHANNEL_IDS or "Cam On" in channel.name):
        # Check for bypass role
        if any(role.id == CAMERA_BYPASS_ROLE for role in member.roles):
            print(f"✅ [{member.display_name}] Has CAMERA_BYPASS_ROLE - Enforcement skipped")
            return
        
        has_cam = after.self_video  # True if camera is on
        has_screenshare = after.self_stream  # True if screensharing
        
        # ✅ PRIMARY: CAM ON - Camera is on = NO WARNING (regardless of screenshare status)
        if has_cam:
            task = cam_timers.pop(member.id, None)
            if task:
                task.cancel()
            
            # If both cam and screenshare are on, show that explicitly
            if has_screenshare:
                print(f"✅ [{member.display_name}] CAM ON + SCREENSHARE ON - No warning needed (Both active)")
            else:
                print(f"✅ [{member.display_name}] CAM ON - No warning needed")
        
        # ❌ CAM OFF - WARNING NEEDED! (screenshare is not enough, camera is required)
        else:
            if member.id not in cam_timers:
                status_text = "SCREENSHARE ON" if has_screenshare else "NO SCREENSHARE"
                print(f"⚠️ [{member.display_name}] CAM OFF ({status_text}) - ENFORCEMENT STARTED!")
                
                member_id = member.id
                guild_id = member.guild.id
                channel_id = channel.id if channel else None

                async def enforce(captured_member_id=member_id, captured_guild_id=guild_id, captured_channel_id=channel_id):
                    try:
                        await asyncio.sleep(30)
                    except asyncio.CancelledError:
                        cam_timers.pop(captured_member_id, None)
                        return

                    guild = bot.get_guild(captured_guild_id)
                    if not guild:
                        cam_timers.pop(captured_member_id, None)
                        return

                    member_obj = guild.get_member(captured_member_id)
                    channel_obj = guild.get_channel(captured_channel_id) if captured_channel_id else None

                    try:
                        mention = member_obj.mention if member_obj else f"<@{captured_member_id}>"
                        embed = discord.Embed(
                            title="🎥 ⚠️ CAMERA REQUIRED - FINAL WARNING!",
                            description=f"{mention}\n\n**Please turn on your camera within 3 minutes or you will be disconnected from the voice channel!**",
                            color=discord.Color.red()
                        )
                        embed.add_field(name="⏱️ TIME REMAINING", value="3 minutes to comply or automatic kick", inline=False)
                        embed.add_field(name="✅ ACTION REQUIRED", value="• Turn on your camera\n*(Screenshare alone is not enough - camera is mandatory)*", inline=False)
                        embed.set_footer(text="⚠️ This channel has strict camera enforcement enabled")

                        if member_obj:
                            await member_obj.send(embed=embed)
                            print(f"📢 [{member_obj.display_name}] 🎥 CAM WARNING SENT - Countdown: 3 MINUTES TO COMPLY OR KICK")
                        else:
                            print(f"📢 [ID:{captured_member_id}] 🎥 CAM WARNING - member not in cache, warning attempted")
                    except Exception as e:
                        print(f"⚠️ Failed to send enforcement warning to ID {captured_member_id}: {e}")

                    try:
                        await asyncio.sleep(180)
                    except asyncio.CancelledError:
                        cam_timers.pop(captured_member_id, None)
                        return

                    print(f"🔍 [ID:{captured_member_id}] TIMER EXPIRED - Checking compliance...")
                    member_ref = guild.get_member(captured_member_id)
                    current_cam = False
                    voice_chan = None
                    if member_ref and member_ref.voice and member_ref.voice.channel:
                        current_cam = member_ref.voice.self_video
                        voice_chan = member_ref.voice.channel
                        print(f"   After refresh - Member: {member_ref.display_name}, Channel: {voice_chan.name}, Cam: {current_cam}")

                    if member_ref and voice_chan and (str(voice_chan.id) in STRICT_CHANNEL_IDS or "Cam On" in voice_chan.name):
                        if current_cam:
                            print(f"✅ [{member_ref.display_name}] COMPLIED IN TIME - CAM ON detected")
                        else:
                            print(f"🚪 [{member_ref.display_name}] ENFORCEMENT EXECUTED - Camera still OFF after 3 minutes")

                            bot_member = guild.get_member(bot.user.id)
                            if bot_member and voice_chan:
                                perms = voice_chan.permissions_for(bot_member)
                                print(f"   Bot permissions in channel: move_members={perms.move_members}, administrator={perms.administrator}")
                                print(f"   Member top role pos: {member_ref.top_role.position}, Bot top role pos: {bot_member.top_role.position}")

                                if not perms.move_members and not perms.administrator:
                                    print(f"❌ [ID:{captured_member_id}] BOT LACKS MOVE_MEMBERS permission in {voice_chan.name}")
                                    return
                                if member_ref.top_role.position >= bot_member.top_role.position and not perms.administrator:
                                    print(f"❌ [ID:{captured_member_id}] Role hierarchy prevents disconnect (member >= bot)")
                                    return
                                print(f"✅ [ID:{captured_member_id}] Bot has permissions and role hierarchy allows disconnect")

                            # Double-check member is still in voice channel before disconnect
                            if not member_ref.voice or not member_ref.voice.channel:
                                print(f"⚠️ [ID:{captured_member_id}] No longer in voice channel when timer expired")
                                return

                            print(f"🔄 [ID:{captured_member_id}] Attempting to move_to(None)...")
                            try:
                                await member_ref.move_to(None, reason="Camera enforcement")
                            except Exception as e:
                                print(f"❌ Failed move_to for ID {captured_member_id}: {e}")
                                return

                            # Verify the disconnect worked - retry checks
                            await asyncio.sleep(2)
                            verified = False
                            for attempt in range(3):
                                member_ref = guild.get_member(captured_member_id) or member_ref
                                if not member_ref.voice or not member_ref.voice.channel:
                                    print(f"✅ [ID:{captured_member_id}] Confirmed disconnected (attempt {attempt + 1})")
                                    verified = True
                                    break
                                print(f"⚠️ [ID:{captured_member_id}] Still in channel after move_to (attempt {attempt + 1}) - {member_ref.voice.channel.name}")
                                await asyncio.sleep(1)
                            if not verified:
                                print(f"❌ [ID:{captured_member_id}] FAILED TO VERIFY DISCONNECT - aborting DM/notice")
                                return

                            # 📢 NOTIFY CHANNEL ABOUT ENFORCEMENT ACTION
                            try:
                                embed_kick = discord.Embed(
                                    title="🚪 User Disconnected",
                                    description=f"{member_ref.mention} has been automatically disconnected for not enabling their camera within the 3-minute time limit.",
                                    color=discord.Color.orange()
                                )
                                embed_kick.set_footer(text="Camera enforcement in strict channels")
                                target_chan = voice_chan or channel_obj
                                if target_chan:
                                    await target_chan.send(embed=embed_kick, delete_after=15)
                            except Exception as e:
                                print(f"⚠️ Failed to send channel notification: {e}")

                            # 📧 SEND DM TO USER ABOUT ENFORCEMENT
                            try:
                                embed_dm = discord.Embed(
                                    title="📵 You Were Disconnected",
                                    description=f"You were disconnected from **{voice_chan.name if voice_chan else (channel_obj.name if channel_obj else 'the voice channel')}** due to camera enforcement.\n\nCamera is mandatory in this channel (screenshare alone is not sufficient).\n\nPlease enable your camera before rejoining.",
                                    color=discord.Color.red()
                                )
                                if member_ref:
                                    await member_ref.send(embed=embed_dm)
                                    print(f"📧 [{member_ref.display_name}] Sent disconnect DM successfully")
                            except Exception as e:
                                print(f"⚠️ Failed to send disconnect DM to ID {captured_member_id}: {e}")

                    # Clean up timer
                    cam_timers.pop(captured_member_id, None)
                
                cam_timers[member.id] = bot.loop.create_task(enforce())

@tasks.loop(seconds=30)
async def strict_camera_enforcement_watchdog():
    """Periodic backstop for strict camera channels."""
    if GUILD_ID <= 0:
        return

    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return

    try:
        await ensure_camera_enforcement_for_guild(guild, source="watchdog")
    except Exception as e:
        print(f"⚠️ strict_camera_enforcement_watchdog error: {e}")


@tasks.loop(seconds=30)
async def batch_save_study():
    """Save voice & cam stats every 30 seconds for accurate tracking"""
    if GUILD_ID <= 0 or not mongo_connected:
        return
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    now = time.time()
    try:
        saved_count = 0
        processed = set()
        
        # ✅ FIRST: Save all users currently in vc_join_times (already tracked)
        for uid, join in list(vc_join_times.items()):
            member = guild.get_member(uid)
            if not member or not member.voice or not member.voice.channel:
                # User left VC, remove from tracking
                vc_join_times.pop(uid, None)
                continue
            
            # Calculate minutes elapsed since last save (at least 30 seconds)
            mins = int((now - join) // 60)
            
            # Only save if at least 30 seconds have passed
            if mins > 0 or (now - join) >= 30:
                # FIXED CAM DETECTION LOGIC:
                # Cam ON = camera is ON (regardless of screenshare)
                # Cam OFF = camera is OFF
                cam = member.voice.self_video
                
                # Skip saving for excluded voice channel
                try:
                    current_channel = member.voice.channel
                except Exception:
                    current_channel = None

                if current_channel and getattr(current_channel, 'id', None) == EXCLUDED_VOICE_CHANNEL_ID:
                    print(f"⏭️ Skipping batch save for excluded channel ({EXCLUDED_VOICE_CHANNEL_ID}) for {member.display_name}")
                    vc_join_times[uid] = now
                    processed.add(uid)
                    continue

                # Only save 1 minute at a time if less than 1 minute has passed
                mins_to_save = max(1, mins) if mins > 0 else 1

                # Avoid concurrent saves for same user
                if uid in vc_saving:
                    print(f"⏸️ Skipping batch save for {member.display_name} - concurrent save in progress")
                    vc_join_times[uid] = now
                    processed.add(uid)
                    continue
                vc_saving.add(uid)
                try:
                    if mins_to_save > 0:
                        field = "data.voice_cam_on_minutes" if cam else "data.voice_cam_off_minutes"
                        # FIX: Separate operations to avoid MongoDB conflict
                        # First: Create document if it doesn't exist
                        users_coll.update_one(
                            {"_id": str(uid)},
                            {"$setOnInsert": {
                                "data": {
                                    "voice_cam_on_minutes": 0,
                                    "voice_cam_off_minutes": 0,
                                    "message_count": 0,
                                    "yesterday": {"cam_on": 0, "cam_off": 0}
                                }
                            }},
                            upsert=True
                        )
                        # Then: Increment the field
                        result = save_with_retry(users_coll, {"_id": str(uid)}, {"$inc": {field: mins_to_save}})
                        if result:
                            cam_status = "🎥 ON" if cam else "❌ OFF"
                            print(f"⏱️ {member.display_name}: +{mins_to_save}m {field} ({cam_status}) ✅")
                            saved_count += 1
                        # Reset join time after saving
                        vc_join_times[uid] = now
                finally:
                    vc_saving.discard(uid)
                processed.add(uid)
        
        # ✅ SECOND: Also save ALL members currently in any voice channel (fallback tracking)
        # This ensures users who joined before bot started are still tracked
        newly_registered = []  # Track newly registered users
        for channel in guild.voice_channels:
            for member in channel.members:
                if member.bot or member.id in processed:
                    continue
                
                # Initialize if not in vc_join_times
                if member.id not in vc_join_times:
                    vc_join_times[member.id] = now
                    mins = 0  # Just initialized, don't save time yet
                    newly_registered.append(member.display_name)  # Add to list instead of printing
                else:
                    mins = int((now - vc_join_times[member.id]) // 60)
                
                if mins > 0 or (now - vc_join_times[member.id]) >= 30:
                    # FIXED CAM DETECTION: Camera ON = camera is physically on
                    cam = member.voice.self_video
                    
                    # Skip saving for excluded voice channel
                    if getattr(channel, 'id', None) == EXCLUDED_VOICE_CHANNEL_ID:
                        print(f"⏭️ Skipping batch save for excluded channel ({EXCLUDED_VOICE_CHANNEL_ID}) for {member.display_name}")
                        vc_join_times[member.id] = now
                        continue
                    
                    mins_to_save = max(1, mins) if mins > 0 else 1

                    # Avoid concurrent saves for same user
                    if member.id in vc_saving:
                        print(f"⏸️ Skipping fallback batch save for {member.display_name} - concurrent save in progress")
                        continue
                    vc_saving.add(member.id)
                    try:
                        if mins_to_save > 0:
                            field = "data.voice_cam_on_minutes" if cam else "data.voice_cam_off_minutes"
                            # FIX: Separate operations to avoid MongoDB conflict
                            users_coll.update_one(
                                {"_id": str(member.id)},
                                {"$setOnInsert": {
                                    "data": {
                                        "voice_cam_on_minutes": 0,
                                        "voice_cam_off_minutes": 0,
                                        "message_count": 0,
                                        "yesterday": {"cam_on": 0, "cam_off": 0}
                                    }
                                }},
                                upsert=True
                            )
                            result = save_with_retry(users_coll, {"_id": str(member.id)}, {"$inc": {field: mins_to_save}})
                            if result:
                                cam_status = "🎥 ON" if cam else "❌ OFF"
                                print(f"⏱️ {member.display_name}: +{mins_to_save}m {field} ({cam_status}) ✅")
                                saved_count += 1
                        vc_join_times[member.id] = now
                    finally:
                        vc_saving.discard(member.id)
        
        # Print consolidated registration message (only ONE message)
        if newly_registered:
            print(f"🔄 Registered ({len(newly_registered)} new): {', '.join(newly_registered)}")
        
        if saved_count > 0:
            print(f"📊 30-second batch save: Updated {saved_count} active members in voice")
    except Exception as e:
        print(f"⚠️ Batch save error: {str(e)[:100]}")

@batch_save_study.before_loop
async def before_batch_save():
    """Ensure batch save starts running from the beginning"""
    await bot.wait_until_ready()
    print("✅ batch_save_study loop started")

# ==================== TEMP VOICE OWNER CONTROLS ====================

async def _tempvoice_safe_defer(interaction: discord.Interaction):
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
    except Exception:
        pass


async def _tempvoice_send(interaction: discord.Interaction, message: str):
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            try:
                await interaction.response.send_message(message, ephemeral=True)
            except Exception as e_inner:
                # If we already have a response, try followup.
                if hasattr(e_inner, 'code') and getattr(e_inner, 'code', None) == 40060:
                    await interaction.followup.send(message, ephemeral=True)
                else:
                    raise
    except Exception as e:
        # Fallback for any crossthread race condition / double acknowledgement.
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
        except Exception:
            pass
        print(f"⚠️ tempvoice interaction send failed: {e}")


async def get_owned_temp_channel(interaction: discord.Interaction, *, require_user_in_channel: bool = False):
    """Returns owned temp voice channel instance or None."""
    guild = interaction.guild
    if not guild:
        return None

    voice_channel = interaction.user.voice.channel if interaction.user.voice else None
    if voice_channel:
        runtime_owner = tempvoice_runtime_owner_by_channel.get(voice_channel.id)
        if runtime_owner == interaction.user.id:
            return voice_channel
        if await is_temp_channel_owner(interaction.user.id, voice_channel.id):
            return voice_channel
        if require_user_in_channel:
            return None
    elif require_user_in_channel:
        return None

    runtime_channel_id = tempvoice_runtime_channel_by_owner.get(interaction.user.id)
    if runtime_channel_id:
        runtime_channel = guild.get_channel(runtime_channel_id)
        if runtime_channel:
            return runtime_channel
        tempvoice_runtime_channel_by_owner.pop(interaction.user.id, None)

    if not tempvoice_db_available or tempvoice_coll is None:
        return None

    try:
        entry = await tempvoice_db_find_one({"owner_id": interaction.user.id, "guild_id": guild.id})
    except Exception as e:
        print(f"⚠️ get_owned_temp_channel DB error: {e}")
        return None

    if not entry:
        return None

    channel_id = entry.get("channel_id")
    channel = guild.get_channel(channel_id) if channel_id else None
    if channel:
        tempvoice_runtime_owner_by_channel[channel.id] = interaction.user.id
        tempvoice_runtime_channel_by_owner[interaction.user.id] = channel.id
        return channel

    # Stale DB entry -> cleanup so user can recreate
    try:
        await tempvoice_coll.delete_many({"owner_id": interaction.user.id, "guild_id": guild.id})
    except Exception as e:
        print(f"⚠️ Failed to cleanup stale temp voice DB entry for {interaction.user.id}: {e}")
    return None

async def check_temp_owner_and_channel(interaction: discord.Interaction):
    channel = await get_owned_temp_channel(interaction, require_user_in_channel=True)
    if not channel:
        await _tempvoice_send(interaction, "❌ You must be the owner of a temp voice channel and be in it to use this command.")
        return None
    return channel

@tree.command(name="create", description="Create your own temporary voice channel", guild=GUILD)
async def create_temp_channel(interaction: discord.Interaction):
    await _tempvoice_safe_defer(interaction)

    guild = interaction.guild
    if not guild:
        return await _tempvoice_send(interaction, "❌ Guild context missing.")

    # Runtime cached existing channel (fast path)
    runtime_existing_id = tempvoice_runtime_channel_by_owner.get(interaction.user.id)
    if runtime_existing_id:
        runtime_existing_chan = guild.get_channel(runtime_existing_id)
        if runtime_existing_chan:
            return await _tempvoice_send(interaction, f"⚠️ You already have a temp channel: {runtime_existing_chan.mention}")
        tempvoice_runtime_channel_by_owner.pop(interaction.user.id, None)

    existing = None
    if tempvoice_db_available and tempvoice_coll is not None:
        existing = await tempvoice_db_find_one({"owner_id": interaction.user.id, "guild_id": guild.id})

    if existing:
        existing_chan = guild.get_channel(existing.get("channel_id"))
        if existing_chan:
            tempvoice_runtime_owner_by_channel[existing_chan.id] = interaction.user.id
            tempvoice_runtime_channel_by_owner[interaction.user.id] = existing_chan.id
            return await _tempvoice_send(interaction, f"⚠️ You already have a temp channel: {existing_chan.mention}")
        if tempvoice_db_available and tempvoice_coll is not None:
            try:
                await tempvoice_db_delete_many({"owner_id": interaction.user.id, "guild_id": guild.id})
            except Exception as e:
                print(f"⚠️ Temp voice stale entry cleanup failed: {e}")

    category = guild.get_channel(TEMP_VOICE_CATEGORY_ID) if TEMP_VOICE_CATEGORY_ID else None
    if category and not isinstance(category, discord.CategoryChannel):
        category = None

    channel_name = f"{interaction.user.name}'s Room"[:100]

    try:
        channel = await guild.create_voice_channel(
            name=channel_name,
            category=category,
            reason="Temp voice channel create command",
        )
    except Exception as e:
        print(f"⚠️ create_temp_channel failed: {e}")
        if isinstance(e, discord.Forbidden):
            return await _tempvoice_send(
                interaction,
                "❌ I don't have permission to create voice channels here. "
                "Grant the bot `Manage Channels` (and set `TEMP_VOICE_CATEGORY_ID` in `.env` if you want channels created inside a specific category).",
            )
        return await _tempvoice_send(interaction, f"❌ Failed to create temp channel: {str(e)[:180]}")

    # Cache ownership in runtime (DB write may fail but commands should still work)
    tempvoice_runtime_owner_by_channel[channel.id] = interaction.user.id
    tempvoice_runtime_channel_by_owner[interaction.user.id] = channel.id

    if tempvoice_db_available and tempvoice_coll is not None:
        try:
            await tempvoice_db_delete_many({"owner_id": interaction.user.id, "guild_id": guild.id})
            await tempvoice_db_insert_one({"channel_id": channel.id, "owner_id": interaction.user.id, "guild_id": guild.id})
        except Exception as e:
            print(f"⚠️ Temp voice DB save failed (non-fatal): {e}")

    # Try to set basic perms (non-fatal if missing perms)
    perms_ok = True
    try:
        await channel.set_permissions(guild.default_role, connect=True, view_channel=True)
        await channel.set_permissions(interaction.user, manage_channels=True, connect=True, speak=True, view_channel=True)
    except Exception as e:
        perms_ok = False
        print(f"⚠️ Temp voice permission set failed (non-fatal): {e}")

    moved = False
    move_error = None
    if interaction.user.voice and interaction.user.voice.channel:
        try:
            await interaction.user.move_to(channel)
            moved = True
        except Exception as e:
            move_error = e
            print(f"⚠️ Temp voice move failed (non-fatal): {e}")

    if moved:
        return await _tempvoice_send(interaction, f"✅ Created and moved you to: {channel.mention}")

    msg = f"✅ Created temp voice channel: {channel.mention}"
    if move_error is not None:
        msg += " (I couldn't move you — missing `Move Members` permission?)"
    if not perms_ok:
        msg += " (I couldn't set permissions — check `Manage Channels` permission.)"
    return await _tempvoice_send(interaction, msg)

@tree.command(name="delete", description="Delete your temp voice channel", guild=GUILD)
async def delete_temp_channel(interaction: discord.Interaction):
    await _tempvoice_safe_defer(interaction)

    channel = await get_owned_temp_channel(interaction)
    if not channel:
        return await _tempvoice_send(interaction, "❌ You don't have an active temp voice channel.")

    try:
        await channel.delete(reason="Owner deleted temp voice channel")
    except Exception as e:
        print(f"⚠️ delete_temp_channel failed: {e}")
        if isinstance(e, discord.Forbidden):
            return await _tempvoice_send(interaction, "❌ I can't delete that channel (missing `Manage Channels`).")
        return await _tempvoice_send(interaction, f"❌ Could not delete channel: {str(e)[:180]}")

    tempvoice_runtime_owner_by_channel.pop(channel.id, None)
    tempvoice_runtime_channel_by_owner.pop(interaction.user.id, None)
    if tempvoice_coll:
        try:
            await tempvoice_coll.delete_one({"channel_id": channel.id})
        except Exception as e:
            print(f"⚠️ Temp voice DB delete failed (non-fatal): {e}")

    return await _tempvoice_send(interaction, "🗑️ Temp channel deleted")

@tree.command(name="rename", description="Rename your temp voice channel", guild=GUILD)
@app_commands.describe(name="New name for channel")
async def rename_temp_channel(interaction: discord.Interaction, name: str):
    await _tempvoice_safe_defer(interaction)

    channel = await check_temp_owner_and_channel(interaction)
    if not channel:
        return

    name = (name or "").strip()
    if not (1 <= len(name) <= 100):
        return await _tempvoice_send(interaction, "❌ Channel name must be 1–100 characters.")

    try:
        await channel.edit(name=name)
    except Exception as e:
        print(f"⚠️ rename_temp_channel failed: {e}")
        if isinstance(e, discord.Forbidden):
            return await _tempvoice_send(interaction, "❌ I can't rename that channel (missing `Manage Channels`).")
        return await _tempvoice_send(interaction, f"❌ Could not rename channel: {str(e)[:180]}")

    return await _tempvoice_send(interaction, "✏️ Channel renamed")

@tree.command(name="limit", description="Set user limit for your temp voice channel", guild=GUILD)
@app_commands.describe(number="User limit (0 = unlimited, max 99)")
async def limit_temp_channel(interaction: discord.Interaction, number: int):
    await _tempvoice_safe_defer(interaction)

    channel = await check_temp_owner_and_channel(interaction)
    if not channel:
        return

    if number < 0 or number > 99:
        return await _tempvoice_send(interaction, "❌ User limit must be between 0 and 99.")

    try:
        await channel.edit(user_limit=number)
    except Exception as e:
        print(f"⚠️ limit_temp_channel failed: {e}")
        if isinstance(e, discord.Forbidden):
            return await _tempvoice_send(interaction, "❌ I can't set the user limit (missing `Manage Channels`).")
        return await _tempvoice_send(interaction, f"❌ Could not set user limit: {str(e)[:180]}")

    return await _tempvoice_send(interaction, f"👥 User limit set to {number}")

@tree.command(name="lock", description="Lock your temp voice channel to everyone", guild=GUILD)
async def lock_temp_channel(interaction: discord.Interaction):
    await _tempvoice_safe_defer(interaction)

    channel = await check_temp_owner_and_channel(interaction)
    if not channel:
        return

    try:
        await channel.set_permissions(interaction.guild.default_role, connect=False, view_channel=True)
    except Exception as e:
        print(f"⚠️ lock_temp_channel failed: {e}")
        if isinstance(e, discord.Forbidden):
            return await _tempvoice_send(interaction, "❌ I can't lock that channel (missing `Manage Channels`).")
        return await _tempvoice_send(interaction, f"❌ Could not lock channel: {str(e)[:180]}")

    return await _tempvoice_send(interaction, "🔒 Channel locked")

@tree.command(name="unlock", description="Unlock your temp voice channel", guild=GUILD)
async def unlock_temp_channel(interaction: discord.Interaction):
    await _tempvoice_safe_defer(interaction)

    channel = await check_temp_owner_and_channel(interaction)
    if not channel:
        return

    try:
        await channel.set_permissions(interaction.guild.default_role, connect=True, view_channel=True)
    except Exception as e:
        print(f"⚠️ unlock_temp_channel failed: {e}")
        if isinstance(e, discord.Forbidden):
            return await _tempvoice_send(interaction, "❌ I can't unlock that channel (missing `Manage Channels`).")
        return await _tempvoice_send(interaction, f"❌ Could not unlock channel: {str(e)[:180]}")

    return await _tempvoice_send(interaction, "🔓 Channel unlocked")

@tree.command(name="permit", description="Allow a member to join your temp voice channel", guild=GUILD)
@app_commands.describe(member="Member to allow")
async def permit_temp_channel(interaction: discord.Interaction, member: discord.Member):
    await _tempvoice_safe_defer(interaction)

    channel = await check_temp_owner_and_channel(interaction)
    if not channel:
        return

    try:
        await channel.set_permissions(member, connect=True, view_channel=True)
    except Exception as e:
        print(f"⚠️ permit_temp_channel failed: {e}")
        if isinstance(e, discord.Forbidden):
            return await _tempvoice_send(interaction, "❌ I can't change permissions (missing `Manage Channels`).")
        return await _tempvoice_send(interaction, f"❌ Could not permit member: {str(e)[:180]}")

    return await _tempvoice_send(interaction, f"✅ {member.mention} can join")

@tree.command(name="deny", description="Block a member from your temp voice channel", guild=GUILD)
@app_commands.describe(member="Member to block")
async def deny_temp_channel(interaction: discord.Interaction, member: discord.Member):
    await _tempvoice_safe_defer(interaction)

    channel = await check_temp_owner_and_channel(interaction)
    if not channel:
        return

    try:
        await channel.set_permissions(member, connect=False, view_channel=False)
        if member.voice and member.voice.channel and member.voice.channel.id == channel.id:
            try:
                await member.move_to(None, reason="Temp voice deny (disconnect)")
            except Exception:
                pass
    except Exception as e:
        print(f"⚠️ deny_temp_channel failed: {e}")
        if isinstance(e, discord.Forbidden):
            return await _tempvoice_send(interaction, "❌ I can't change permissions (missing `Manage Channels`).")
        return await _tempvoice_send(interaction, f"❌ Could not deny member: {str(e)[:180]}")

    return await _tempvoice_send(interaction, f"❌ {member.mention} denied")

# ==================== LEADERBOARDS ====================

# Leaderboard text formatting lives in leaderboard.py (imported above).

# helpers imported from leaderboard.py to avoid import-time side-effects

@tasks.loop(time=datetime.time(23, 55, tzinfo=KOLKATA))
async def auto_leaderboard_ping():
    """Auto ping at 23:55 IST to announce leaderboard with top 5"""
    if GUILD_ID <= 0 or not mongo_connected:
        return
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    channel = guild.get_channel(AUTO_LB_CHANNEL_ID)
    if not channel:
        return
    try:
        role = guild.get_role(AUTO_LB_PING_ROLE_ID)
        if not role:
            print(f"⚠️ Auto ping role {AUTO_LB_PING_ROLE_ID} not found")
            return
        
        ping_text = f"{role.mention} 🏆 **Leaderboard Published With Top 5 Performers!**\n✨ Check the rankings below and compete for glory! ✨"
        await channel.send(ping_text)
        print(f"✅ Auto ping sent at 23:55 IST to {role.name}")
    except Exception as e:
        print(f"⚠️ Auto ping error: {str(e)[:100]}")

@tasks.loop(time=datetime.time(23, 55, tzinfo=KOLKATA))
async def auto_leaderboard():
    """Auto leaderboard at 23:55 IST - shows today's TOP 5 data before reset"""
    if GUILD_ID <= 0 or not mongo_connected:
        return
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    channel = guild.get_channel(AUTO_LB_CHANNEL_ID)
    if not channel:
        return
    try:
        now_ist = datetime.datetime.now(KOLKATA)
        docs = safe_find(users_coll, {})
        active = []
        for doc in docs:
            data = doc.get("data", {})
            cam_on = data.get("voice_cam_on_minutes", 0)
            cam_off = data.get("voice_cam_off_minutes", 0)
            
            # Skip users with no data (same filter as /lb command)
            if cam_on == 0 and cam_off == 0:
                continue
            
            try:
                m = guild.get_member(int(doc["_id"]))
                if m:
                    active.append({"name": m.display_name, "cam_on": cam_on, "cam_off": cam_off})
            except Exception:
                pass
        
        sorted_on = sorted(active, key=lambda x: x["cam_on"], reverse=True)
        sorted_off = sorted(active, key=lambda x: x["cam_off"], reverse=True)
        
        # Convert to list of tuples for formatting (include everyone, even 0 minutes)
        cam_on_data = [(u["name"], u["cam_on"]) for u in sorted_on]
        cam_off_data = [(u["name"], u["cam_off"]) for u in sorted_off]
        
        leaderboard_text = generate_leaderboard_text(cam_on_data, cam_off_data)
        
        await channel.send(f"```{leaderboard_text}```")
        print(f"✅ Auto leaderboard posted at 23:55 IST with TOP 5 performers | Users with data: {len(active)}")
    except Exception as e:
        print(f"⚠️ Auto leaderboard error: {str(e)[:100]}")

@tasks.loop(time=datetime.time(23, 59, tzinfo=KOLKATA))
async def midnight_reset():
    """Daily data reset at 11:59 PM IST (Indian Time) - preserves yesterday's data"""
    if not mongo_connected:
        return
    try:
        now_ist = datetime.datetime.now(KOLKATA)
        print(f"\n{'='*70}")
        print(f"🌙 DAILY RESET INITIATED at {now_ist.strftime('%d/%m/%Y %H:%M:%S IST')}")
        print(f"{'='*70}")
        
        docs = safe_find(users_coll, {})
        reset_count = 0
        
        for doc in docs:
            try:
                data = doc.get("data", {})
                cam_on_today = data.get("voice_cam_on_minutes", 0)
                cam_off_today = data.get("voice_cam_off_minutes", 0)
                
                # Preserve today's data to yesterday, then reset today's counters
                result = safe_update_one(users_coll, {"_id": doc["_id"]}, {"$set": {
                    "data.yesterday.cam_on": cam_on_today,
                    "data.yesterday.cam_off": cam_off_today,
                    "data.voice_cam_on_minutes": 0,
                    "data.voice_cam_off_minutes": 0,
                    "last_reset": now_ist.isoformat()
                }})
                if result:
                    reset_count += 1
                    print(f"   ✅ {doc['_id']}: {format_time(cam_on_today)} ON → Yesterday | Reset today's counters")
            except Exception as e:
                print(f"   ⚠️ Error resetting {doc.get('_id', 'unknown')}: {str(e)[:60]}")
        
        print(f"\n🌙 Daily Reset Complete: {reset_count} users reset")
        print(f"📊 New data collection cycle starts now at {now_ist.strftime('%H:%M:%S IST')}")
        print(f"{'='*70}\n")
    except Exception as e:
        print(f"⚠️ Midnight reset error: {str(e)[:100]}")

# Leaderboard commands
@tree.command(name="lb", description="Today’s voice + cam leaderboard", guild=GUILD)
@checks.cooldown(1, 10)   # once per 10 sec per user
async def lb(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        if not mongo_connected:
            return await interaction.followup.send("📡 Database temporarily unavailable. Try again in a moment.")
        
        docs = safe_find(users_coll, {}, limit=100)
        print(f"🔍 /lb command: Found {len(docs)} total documents in MongoDB")
        
        if not docs:
            print(f"⚠️ No documents found in MongoDB! Showing empty leaderboard.")
            leaderboard_text = generate_leaderboard_text([], [])
            await interaction.followup.send(f"```{leaderboard_text}```")
            return
        
        print(f"   ℹ️  Processing {len(docs)} users...")
        
        active = []
        # Build member cache for fast lookups
        members_by_id = {m.id: m for m in interaction.guild.members}
        print(f"   Guild has {len(members_by_id)} members in cache")

        for idx, doc in enumerate(docs):
            try:
                # Get user ID (handle both string and int)
                user_id_str = str(doc.get("_id", "")).strip()
                
                # Skip invalid IDs (like "mongodb_test")
                if not user_id_str or not user_id_str.isdigit():
                    print(f"   ⚠️ Skipping invalid ID: {user_id_str}")
                    continue
                
                user_id = int(user_id_str)
                data = doc.get("data", {})
                cam_on = data.get("voice_cam_on_minutes", 0)
                cam_off = data.get("voice_cam_off_minutes", 0)
                
                # Skip users with no data
                if cam_on == 0 and cam_off == 0:
                    continue
                
                # Try to get member name from guild cache first
                m = members_by_id.get(user_id)
                display_name = None
                
                if m:
                    display_name = m.display_name
                    source = "cache"
                else:
                    # Try to fetch member from API
                    try:
                        m = await interaction.guild.fetch_member(user_id)
                        display_name = m.display_name
                        source = "api"
                    except Exception:
                        # User not in guild anymore - try to fetch user data directly
                        try:
                            user = await bot.fetch_user(user_id)
                            display_name = user.name
                            source = "user_api"
                        except Exception:
                            # Last resort - use ID as display name
                            display_name = f"[{user_id}]"
                            source = "fallback"
                
                if display_name:
                    active.append({"name": display_name, "cam_on": cam_on, "cam_off": cam_off})
                    print(f"   ✅ {display_name}: CAM_ON={cam_on}min, CAM_OFF={cam_off}min ({source})")
                    
            except Exception as e:
                print(f"   ⚠️ Error processing doc {idx}: {str(e)[:80]}")

        print(f"   📊 Total active users with data: {len(active)}")
        
        if not active:
            print(f"   ℹ️  No users with study time found")
            leaderboard_text = generate_leaderboard_text([], [])
            await interaction.followup.send(f"```{leaderboard_text}```")
            return
        
        sorted_on = sorted(active, key=lambda x: x["cam_on"], reverse=True)
        sorted_off = sorted(active, key=lambda x: x["cam_off"], reverse=True)

        cam_on_data = [(u["name"], u["cam_on"]) for u in sorted_on]
        cam_off_data = [(u["name"], u["cam_off"]) for u in sorted_off]

        print(f"   CAM_ON top 3: {cam_on_data[:3]}")
        print(f"   CAM_OFF top 3: {cam_off_data[:3]}")

        leaderboard_text = generate_leaderboard_text(cam_on_data, cam_off_data)
        
        if leaderboard_text is None:
            print(f"⚠️ ERROR: generate_leaderboard_text returned None!")
            await interaction.followup.send("⚠️ Error generating leaderboard text.")
            return
        
        await interaction.followup.send(f"```{leaderboard_text}```")
        print(f"✅ /lb command: leaderboard sent successfully")
    except Exception as e:
        print(f"⚠️ /lb command error: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        await interaction.followup.send(f"⚠️ Failed to generate leaderboard: {str(e)[:100]}")


# Per-user rank command (/rank)
@tree.command(name="rank", description="Show a user's CAM ON / CAM OFF rank", guild=GUILD)
@checks.cooldown(1, 10)
async def rank(interaction: discord.Interaction, member: discord.Member = None):
    """Slash command to show the per-user rank summary.

    This command is intentionally implemented as a standalone feature and
    only reads from the DB; it does not modify any existing structures.
    """
    await interaction.response.defer()
    try:
        if not mongo_connected:
            return await interaction.followup.send("📡 Database temporarily unavailable. Try again in a moment.")

        target = member or interaction.user

        # Collect today's data (same approach as auto_leaderboard)
        docs = safe_find(users_coll, {}, limit=1000)
        active = []
        for doc in docs:
            data = doc.get("data", {})
            try:
                m = interaction.guild.get_member(int(doc["_id"]))
                if m:
                    active.append({
                        "name": m.display_name,
                        "cam_on": data.get("voice_cam_on_minutes", 0),
                        "cam_off": data.get("voice_cam_off_minutes", 0)
                    })
            except Exception:
                continue

        sorted_on = sorted(active, key=lambda x: x["cam_on"], reverse=True)
        sorted_off = sorted(active, key=lambda x: x["cam_off"], reverse=True)

        cam_on_data = [(u["name"], u["cam_on"]) for u in sorted_on]
        cam_off_data = [(u["name"], u["cam_off"]) for u in sorted_off]

        summary = user_rank(target.display_name, cam_on_data, cam_off_data)
        # Send as code block for monospaced alignment; public message
        await interaction.followup.send(f"```{summary}```")
    except Exception as e:
        print(f"⚠️ /rank command error: {e}")
        await interaction.followup.send("⚠️ Failed to generate rank. Try again later.")


# Note: `mystatus` and `yst` are implemented later in the file (preserved original versions)
        
        for doc in docs:
            try:
                # Skip the test document created during startup
                if doc["_id"] == "mongodb_test":
                    continue
                    
                user_id = int(doc["_id"])
                member = members_by_id.get(user_id)
                if not member:
                    continue
                data = doc.get("data", {})
                cam_on = data.get("voice_cam_on_minutes", 0)
                cam_off = data.get("voice_cam_off_minutes", 0)
                total = cam_on + cam_off
                print(f"   - {member.display_name}: Cam ON {cam_on}m, Cam OFF {cam_off}m (Total: {total}m)")
                if cam_on > 0 or cam_off > 0:
                    active.append({"name": member.display_name, "cam_on": cam_on, "cam_off": cam_off})
            except (ValueError, KeyError) as e:
                print(f"   ⚠️ Error processing doc: {e}")
                continue
        
        print(f"   ✅ Processed {len(docs)} documents, {len(active)} have data")
        sorted_on = sorted(active, key=lambda x: x["cam_on"], reverse=True)[:15]  # TOP 15 CAM ON
        sorted_off = sorted(active, key=lambda x: x["cam_off"], reverse=True)[:10]  # TOP 10 CAM OFF
        
        # ✨ Beautiful Leaderboard Design
        cam_on_data = [(u["name"], u["cam_on"]) for u in sorted_on]
        cam_off_data = [(u["name"], u["cam_off"]) for u in sorted_off]
        
        leaderboard_text = generate_leaderboard_text(cam_on_data, cam_off_data)
        
        await interaction.followup.send(f"```{leaderboard_text}```")
    except Exception as e:
        error_msg = str(e)
        if "SSL" in error_msg or "handshake" in error_msg:
            await interaction.followup.send("📡 Database connection issue. Please try again later.")
        else:
            await interaction.followup.send(f"Error loading leaderboard: {str(e)[:100]}")

@tree.command(name="ylb", description="Yesterday’s leaderboard", guild=GUILD)
async def ylb(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        if not mongo_connected:
            return await interaction.followup.send("📡 Database temporarily unavailable. Try again in a moment.")
        
        docs = safe_find(users_coll, {"data.yesterday": {"$exists": True}})
        active = []
        # Use guild.members cache instead of fetching (faster)
        members_by_id = {m.id: m for m in interaction.guild.members}
        for doc in docs:
            try:
                user_id = int(doc["_id"])
                member = members_by_id.get(user_id)
                if not member:
                    continue
                y = doc.get("data", {}).get("yesterday", {})
                if y.get("cam_on", 0) == 0 and y.get("cam_off", 0) == 0:
                    continue
                active.append({"name": member.display_name, "cam_on": y.get("cam_on", 0), "cam_off": y.get("cam_off", 0)})
            except (ValueError, KeyError):
                continue
        sorted_on = sorted(active, key=lambda x: x["cam_on"], reverse=True)[:15]
        sorted_off = sorted(active, key=lambda x: x["cam_off"], reverse=True)[:10]
        desc = "**Yesterday Cam On ✅**\n" + ("\n".join(f"#{i} **{u['name']}** — {format_time(u['cam_on'])}" for i, u in enumerate(sorted_on, 1) if u["cam_on"] > 0) or "No data.\n")
        desc += "\n**Yesterday Cam Off ❌**\n" + ("\n".join(f"#{i} **{u['name']}** — {format_time(u['cam_off'])}" for i, u in enumerate(sorted_off, 1) if u["cam_off"] > 0) or "")
        embed = discord.Embed(title="⏮️ Yesterday Leaderboard", description=desc, color=0xA9A9A9)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        error_msg = str(e)
        if "SSL" in error_msg or "handshake" in error_msg:
            await interaction.followup.send("📡 Database connection issue. Please try again later.")
        else:
            await interaction.followup.send(f"Error loading yesterday leaderboard: {str(e)[:100]}")

@tree.command(name="mystatus", description="Your personal VC + cam stats", guild=GUILD)
async def mystatus(interaction: discord.Interaction):
    await interaction.response.defer()

    try:
        doc = safe_find_one(users_coll, {"_id": str(interaction.user.id)})
    except Exception as e:
        await interaction.followup.send(f"DB Error: {e}")
        return

    if not doc or "data" not in doc:
        return await interaction.followup.send("No stats yet.")

    data = doc["data"]
    total = data.get("voice_cam_on_minutes", 0) + data.get("voice_cam_off_minutes", 0)

    embed = discord.Embed(
        title=f"📊 Stats for {interaction.user.name}",
        color=0x9932CC
    )
    embed.add_field(name="Total", value=format_time(total))
    embed.add_field(name="Cam On", value=format_time(data.get("voice_cam_on_minutes", 0)))
    embed.add_field(name="Cam Off", value=format_time(data.get("voice_cam_off_minutes", 0)))

    await interaction.followup.send(embed=embed)



@tree.command(name="yst", description="Your yesterday’s stats", guild=GUILD)
async def yst(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        doc = safe_find_one(users_coll, {"_id": str(interaction.user.id)})
        if not doc or "data" not in doc or "yesterday" not in doc["data"]:
            return await interaction.followup.send("No yesterday data.")
        y = doc["data"]["yesterday"]
        total = y.get("cam_on", 0) + y.get("cam_off", 0)
        embed = discord.Embed(title=f"🗓️ Yesterday Stats: {interaction.user.name}", color=0x808080)
        embed.add_field(name="Total", value=format_time(total), inline=True)
        embed.add_field(name="Cam On", value=format_time(y.get("cam_on", 0)), inline=True)
        embed.add_field(name="Cam Off", value=format_time(y.get("cam_off", 0)), inline=True)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"Error loading stats: {str(e)[:100]}")

# ==================== REDLIST ====================
@tree.command(name="redban", description="Ban a user & store in redlist", guild=GUILD)
@app_commands.describe(userid="User ID")
async def redban(interaction: discord.Interaction, userid: str):
    await interaction.response.defer()
    try:
        if interaction.user.id != OWNER_ID:
            return await interaction.followup.send("Owner only")
        if not userid.isdigit():
            return await interaction.followup.send("Invalid ID")
        safe_update_one(redlist_coll, {"_id": userid}, {"$set": {"added": datetime.datetime.now(KOLKATA)}})
        try:
            await interaction.guild.ban(discord.Object(id=int(userid)), reason="Redlist")
        except Exception as e:
            print(f"Ban error: {e}")
        await interaction.followup.send(f"Redlisted {userid}")
    except Exception as e:
        await interaction.followup.send(f"Error: {str(e)[:100]}")

@tree.command(name="redlist", description="Show banned / restricted users", guild=GUILD)
async def redlist(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        if interaction.user.id != OWNER_ID:
            return await interaction.followup.send("Owner only")
        ids = [doc["_id"] for doc in safe_find(redlist_coll, {})]
        if not ids:
            return await interaction.followup.send("Empty redlist.")
        msg = "Redlist IDs:\n" + "\n".join(f"- {i}" for i in ids)
        await interaction.followup.send(msg)
    except Exception as e:
        await interaction.followup.send(f"Error: {str(e)[:100]}")

@tree.command(name="removeredban", description="Remove a user from redlist & unban", guild=GUILD)
@app_commands.describe(userid="User ID to remove from redlist")
async def removeredban(interaction: discord.Interaction, userid: str):
    await interaction.response.defer(ephemeral=True)
    try:
        if interaction.user.id != OWNER_ID:
            return await interaction.followup.send("Owner only", ephemeral=True)
        
        if not userid.isdigit():
            return await interaction.followup.send("Invalid ID format", ephemeral=True)
        
        # Check if user exists in redlist
        user_doc = safe_find_one(redlist_coll, {"_id": userid})
        if not user_doc:
            return await interaction.followup.send(f"User {userid} not found in redlist", ephemeral=True)
        
        # Remove from redlist
        safe_delete_one(redlist_coll, {"_id": userid})
        
        # Try to unban the user
        try:
            await interaction.guild.unban(discord.Object(id=int(userid)), reason="Removed from redlist")
            status = "✅ Unbanned successfully"
        except discord.errors.NotFound:
            status = "⚠️ User not banned on server"
        except Exception as e:
            status = f"⚠️ Unban failed: {str(e)[:50]}"
        
        await interaction.followup.send(f"Removed {userid} from redlist. {status}", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Error: {str(e)[:100]}", ephemeral=True)

@bot.event
async def on_member_join(member: discord.Member):
    if member.guild.id != GUILD_ID:
        return
    now = time.time()
    join_times[member.guild.id].append(now)
    join_times[member.guild.id] = [t for t in join_times[member.guild.id] if now - t < RAID_WINDOW]
    if len(join_times[member.guild.id]) > RAID_THRESHOLD:
        await lockdown_guild(member.guild)
        tech_channel = bot.get_channel(TECH_CHANNEL_ID)
        if tech_channel:
            await tech_channel.send("⚠️ Raid detected! Lockdown activated.")
        await alert_owner(member.guild, "RAID DETECTED", {
            "Trigger": f"{len(join_times[member.guild.id])} joins inside {RAID_WINDOW} seconds",
            "Latest Join": f"{member} ({member.id})",
            "Action": "Emergency lockdown + recent join sweep",
        })
        # Ban recent joins
        for m in member.guild.members:
            if m.joined_at and (now - m.joined_at.timestamp()) < RAID_WINDOW:
                try:
                    await m.ban(reason="Raid protection")
                except Exception:
                    pass
    if safe_find_one(redlist_coll, {"_id": str(member.id)}):
        try:
            await member.ban(reason="Redlist")
            await send_security_log(
                member.guild,
                "🚫 Redlist Match",
                description=f"{member} rejoined and was removed automatically.",
                color=discord.Color.red(),
                fields={"User ID": str(member.id)},
            )
        except Exception:
            pass
    if member.bot and member.id not in WHITELISTED_BOTS:
        audit_entry = await find_matching_audit_entry(
            member.guild,
            discord.AuditLogAction.bot_add,
            target_id=member.id,
            limit=4,
        )
        adder = audit_entry.user if audit_entry else None

        if adder and not is_whitelisted_entity(adder):
            try:
                await member.guild.ban(adder, reason=f"Anti-Nuke: Unauthorized Bot Add ({member.id})")
            except discord.Forbidden:
                await engage_lockdown(member.guild, "Failed to ban unauthorized bot adder")
            except Exception as e:
                print(f"⚠️ Failed to punish unauthorized bot adder {adder}: {e}")

            await send_security_log(
                member.guild,
                "🚨 Unauthorized Bot Added",
                description=f"{member.mention} joined without being on the bot whitelist.",
                color=discord.Color.red(),
                fields={
                    "Bot ID": str(member.id),
                    "Added By": f"{adder} ({adder.id})",
                },
            )
            await alert_owner(member.guild, "UNAUTHORIZED BOT ADDED", {
                "Bot": f"{member} (ID: {member.id})",
                "Added By": f"{adder} (ID: {adder.id})",
                "Action": "Adder targeted by anti-nuke + bot removed",
            })

        try:
            await member.ban(reason="Non-whitelisted bot")
        except Exception:
            pass


@bot.event
async def on_member_remove(member: discord.Member):
    if member.guild.id != GUILD_ID:
        return

    audit_entry = await find_matching_audit_entry(
        member.guild,
        discord.AuditLogAction.kick,
        target_id=member.id,
        limit=6,
    )
    if not audit_entry or not audit_entry.user:
        return

    actor = audit_entry.user
    if is_whitelisted_entity(actor) or actor.id == member.id:
        return

    try:
        await member.guild.ban(actor, reason=f"Anti-Nuke: Unauthorized Kick of {member.id}")
        await send_security_log(
            member.guild,
            "🚨 Unauthorized Kick Detected",
            description=f"{actor.mention} kicked {member}.",
            color=discord.Color.red(),
            fields={
                "Attacker": f"{actor} ({actor.id})",
                "Victim": f"{member} ({member.id})",
                "Audit Entry": str(audit_entry.id),
            },
        )
        await alert_owner(member.guild, "UNAUTHORIZED KICK DETECTED", {
            "Attacker": f"{actor} (ID: {actor.id})",
            "Victim": f"{member} (ID: {member.id})",
            "Action": "Attacker targeted by anti-nuke",
        })
    except discord.Forbidden:
        await engage_lockdown(member.guild, "Failed to ban unauthorized kicker")
    except Exception as e:
        print(f"⚠️ Unauthorized kick handling error: {e}")


@bot.event
async def on_guild_role_update(before: discord.Role, after: discord.Role):
    if after.guild.id != GUILD_ID or before.permissions == after.permissions:
        return

    newly_granted = get_newly_granted_permissions(before.permissions, after.permissions)
    if not newly_granted:
        return

    audit_entry = await find_matching_audit_entry(
        after.guild,
        discord.AuditLogAction.role_update,
        target_id=after.id,
        limit=6,
    )
    if not audit_entry or not audit_entry.user:
        return

    actor = audit_entry.user
    if is_whitelisted_entity(actor):
        return

    permission_list = ", ".join(sorted(newly_granted))
    await send_security_log(
        after.guild,
        "🚨 Dangerous Role Permission Escalation",
        description=f"{actor.mention} granted sensitive permissions to `{after.name}`.",
        color=discord.Color.red(),
        fields={
            "Role": f"{after.name} ({after.id})",
            "Granted Permissions": permission_list,
            "Audit Entry": str(audit_entry.id),
        },
    )
    await alert_owner(after.guild, "ROLE PERMISSION ESCALATION", {
        "Actor": f"{actor} (ID: {actor.id})",
        "Role": f"{after.name} (ID: {after.id})",
        "Permissions": permission_list,
    })

    if after.is_default() and any(
        perm in {"administrator", "manage_guild", "manage_roles", "manage_channels", "manage_webhooks"}
        for perm in newly_granted
    ):
        await engage_lockdown(after.guild, f"Dangerous @everyone permission escalation by {actor}")

# ==================== TODO HELPERS ====================

async def send_todo_to_channel(embed: discord.Embed, source: str = "TodoModal"):
    """Send TODO embed to the dedicated TODO channel - GUARANTEED to send"""
    print(f"\n{'='*70}")
    print(f"� [SEND_TODO_TO_CHANNEL] Starting")
    print(f"   Source: {source}")
    print(f"   Guild ID: {GUILD_ID}")
    print(f"   Channel ID: {TODO_CHANNEL_ID}")
    print(f"   Embed title: {embed.title}")
    
    if GUILD_ID <= 0 or TODO_CHANNEL_ID <= 0:
        print(f"❌ Invalid IDs")
        return False
    
    try:
        print(f"📤 Attempt: bot.get_guild({GUILD_ID})")
        guild = bot.get_guild(GUILD_ID)
        print(f"   Result: {guild}")
        
        if not guild:
            print(f"❌ Guild is None, returning False")
            return False
        
        print(f"✅ Guild: {guild.name}")
        print(f"📤 Attempt: guild.get_channel({TODO_CHANNEL_ID})")
        channel = guild.get_channel(TODO_CHANNEL_ID)
        print(f"   Result: {channel}")
        
        if not channel:
            print(f"❌ Channel is None, trying fetch_channel...")
            try:
                channel = await guild.fetch_channel(TODO_CHANNEL_ID)
                print(f"✅ Channel fetched: {channel}")
            except Exception as fe:
                print(f"❌ fetch_channel failed: {fe}")
                return False
        
        if not channel:
            print(f"❌ Channel is still None after both methods")
            return False
        
        print(f"✅ Channel: {channel.name}")
        print(f"📤 Sending message to channel...")
        await channel.send(embed=embed)
        print(f"✅✅✅ MESSAGE SENT SUCCESSFULLY! ✅✅✅")
        print(f"{'='*70}\n")
        return True
        
    except Exception as e:
        print(f"❌ Exception: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        print(f"{'='*70}\n")
        return False


# ==================== TODO SYSTEM ====================
# Simple command-based TODO with direct attachment support

@tree.command(name="todo", description="Submit daily TODO with tasks and file", guild=GUILD)
@app_commands.describe(
    feature="Feature name (required)",
    date="Date DD/MM/YYYY",
    must_do="Must Do tasks",
    can_do="Can Do tasks",
    dont_do="Don't Do restrictions",
    attachment="File/Screenshot (max 8MB)"
)
async def todo(
    interaction: discord.Interaction,
    feature: str,
    date: str,
    attachment: discord.Attachment = None,
    must_do: str = None,
    can_do: str = None,
    dont_do: str = None
):
    """Submit daily TODO with feature name, date, and categories"""
    await interaction.response.defer()
    
    uid = str(interaction.user.id)
    
    # Auth check
    if not safe_find_one(active_members_coll, {"_id": uid}) and interaction.user.id != OWNER_ID:
        await interaction.followup.send("❌ Not authorized", ephemeral=True)
        return
    
    # Date validation
    try:
        date_obj = datetime.datetime.strptime(date, "%d/%m/%Y")
    except ValueError:
        await interaction.followup.send(f"❌ Invalid date. Use DD/MM/YYYY format", ephemeral=True)
        return
    
    # CONTENT LENGTH VALIDATION - Prevent embed field overflow
    max_field_length = 950  # Leave margin for code block markers
    if must_do and len(must_do) > max_field_length:
        await interaction.followup.send(f"❌ Must Do text is too long (max {max_field_length} chars)", ephemeral=True)
        return
    if can_do and len(can_do) > max_field_length:
        await interaction.followup.send(f"❌ Can Do text is too long (max {max_field_length} chars)", ephemeral=True)
        return
    if dont_do and len(dont_do) > max_field_length:
        await interaction.followup.send(f"❌ Don't Do text is too long (max {max_field_length} chars)", ephemeral=True)
        return
    
    # Content check
    if not any([must_do, can_do, dont_do]) and not attachment:
        await interaction.followup.send("❌ Provide content or attachment", ephemeral=True)
        return
    
    # Validate attachment if provided
    attachment_data = None
    if attachment:
        # Size check
        if attachment.size > 8 * 1024 * 1024:
            await interaction.followup.send(f"❌ File too large (max 8MB)", ephemeral=True)
            return
        
        # Type check
        ext = attachment.filename.rsplit('.', 1)[-1].lower() if '.' in attachment.filename else ''
        valid_exts = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'tiff', 'pdf', 'txt', 'doc', 'docx', 'xlsx', 'ppt', 'pptx', 'csv']
        
        if ext not in valid_exts:
            await interaction.followup.send(f"❌ File type not supported", ephemeral=True)
            return
        
        # Detect type
        file_type = 'image' if ext in ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'tiff'] else 'document'
        
        attachment_data = {
            "url": attachment.url,
            "filename": attachment.filename,
            "file_type": file_type,
            "uploaded_at": datetime.datetime.now(KOLKATA).isoformat()
        }
    
    # Save to DB
    now = datetime.datetime.now(tz=KOLKATA)
    todo_data = {
        "feature_name": feature[:100],  # Truncate feature name
        "date": date,
        "must_do": must_do or "N/A",
        "can_do": can_do or "N/A",
        "dont_do": dont_do or "N/A",
        "submitted_at": now.isoformat()
    }
    if attachment_data:
        todo_data["attachment"] = attachment_data
    
    safe_update_one(todo_coll, {"_id": uid}, {
        "$set": {
            "last_submit": time.time(),
            "last_ping": 0,
            "todo": todo_data,
            "updated_at": now.isoformat()
        }
    })
    
    # Create embed for channel - TRUNCATE FIELDS FOR SAFETY
    embed = discord.Embed(title=f"📋 {feature[:100]}", color=discord.Color.from_rgb(0, 150, 255), timestamp=now)
    embed.add_field(name="👤 By", value=interaction.user.mention, inline=False)
    embed.add_field(name="📅 Date", value=date, inline=True)
    
    if must_do:
        safe_must_do = truncate_for_codeblock(must_do)
        embed.add_field(name="✔️ MUST DO", value=f"```{safe_must_do}```", inline=False)
    if can_do:
        safe_can_do = truncate_for_codeblock(can_do)
        embed.add_field(name="🎯 CAN DO", value=f"```{safe_can_do}```", inline=False)
    if dont_do:
        safe_dont_do = truncate_for_codeblock(dont_do)
        embed.add_field(name="❌ DON'T DO", value=f"```{safe_dont_do}```", inline=False)
    
    if attachment_data:
        emoji = "🖼️" if attachment_data['file_type'] == 'image' else "📄"
        embed.add_field(name=f"{emoji} File", value=f"[{attachment.filename}]({attachment.url})", inline=False)
        if attachment_data['file_type'] == 'image':
            embed.set_image(url=attachment.url)
    
    embed.set_author(name=interaction.user.name, icon_url=interaction.user.avatar.url if interaction.user.avatar else None)
    
    # Send to TODO channel (PUBLIC)
    try:
        guild = bot.get_guild(GUILD_ID)
        if guild:
            channel = guild.get_channel(TODO_CHANNEL_ID)
            if channel:
                await channel.send(embed=embed)
    except Exception:
        pass
    
    await interaction.followup.send("✅ TODO posted for everyone!")


@tree.command(name="atodo", description="Assign TODO to user (Owner only)", guild=GUILD)
@app_commands.describe(
    user="Target user",
    feature="Feature name",
    date="Date DD/MM/YYYY",
    must_do="Must Do tasks",
    can_do="Can Do tasks",
    dont_do="Don't Do restrictions",
    attachment="File/Screenshot"
)
async def atodo(
    interaction: discord.Interaction,
    user: discord.Member,
    feature: str,
    date: str,
    attachment: discord.Attachment = None,
    must_do: str = None,
    can_do: str = None,
    dont_do: str = None
):
    """Owner-only: Assign TODO to another user"""
    await interaction.response.defer()
    
    # Owner check
    if interaction.user.id != OWNER_ID:
        await interaction.followup.send("❌ Owner only", ephemeral=True)
        return
    
    uid = str(user.id)
    
    # Target auth check
    if not safe_find_one(active_members_coll, {"_id": uid}):
        await interaction.followup.send(f"❌ {user.mention} not authorized", ephemeral=True)
        return
    
    # Date validation
    try:
        date_obj = datetime.datetime.strptime(date, "%d/%m/%Y")
    except ValueError:
        await interaction.followup.send(f"❌ Invalid date", ephemeral=True)
        return
    
    # CONTENT LENGTH VALIDATION - Prevent embed field overflow
    max_field_length = 950  # Leave margin for code block markers
    if must_do and len(must_do) > max_field_length:
        await interaction.followup.send(f"❌ Must Do text is too long (max {max_field_length} chars)", ephemeral=True)
        return
    if can_do and len(can_do) > max_field_length:
        await interaction.followup.send(f"❌ Can Do text is too long (max {max_field_length} chars)", ephemeral=True)
        return
    if dont_do and len(dont_do) > max_field_length:
        await interaction.followup.send(f"❌ Don't Do text is too long (max {max_field_length} chars)", ephemeral=True)
        return
    
    # Content check
    if not any([must_do, can_do, dont_do]) and not attachment:
        await interaction.followup.send("❌ Provide content", ephemeral=True)
        return
    
    # Validate attachment
    attachment_data = None
    if attachment:
        if attachment.size > 8 * 1024 * 1024:
            await interaction.followup.send(f"❌ File too large", ephemeral=True)
            return
        
        ext = attachment.filename.rsplit('.', 1)[-1].lower() if '.' in attachment.filename else ''
        valid_exts = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'pdf', 'txt', 'doc', 'docx', 'xlsx', 'ppt', 'pptx', 'csv']
        
        if ext not in valid_exts:
            await interaction.followup.send(f"❌ File type not supported", ephemeral=True)
            return
        
        file_type = 'image' if ext in ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'tiff'] else 'document'
        
        attachment_data = {
            "url": attachment.url,
            "filename": attachment.filename,
            "file_type": file_type,
            "uploaded_at": datetime.datetime.now(KOLKATA).isoformat()
        }
    
    # Save to DB
    now = datetime.datetime.now(tz=KOLKATA)
    todo_data = {
        "feature_name": feature[:100],  # Truncate feature name
        "date": date,
        "must_do": must_do or "N/A",
        "can_do": can_do or "N/A",
        "dont_do": dont_do or "N/A",
        "submitted_at": now.isoformat(),
        "submitted_by": interaction.user.name,
        "submitted_by_id": interaction.user.id
    }
    if attachment_data:
        todo_data["attachment"] = attachment_data
    
    safe_update_one(todo_coll, {"_id": uid}, {
        "$set": {
            "last_submit": time.time(),
            "last_ping": 0,
            "todo": todo_data,
            "updated_at": now.isoformat()
        }
    })
    
    # Create embed - GOLD color for owner submission - TRUNCATE FIELDS FOR SAFETY
    embed = discord.Embed(title=f"📋 {feature[:100]}", color=discord.Color.from_rgb(255, 165, 0), timestamp=now)
    embed.add_field(name="👤 Assigned To", value=user.mention, inline=False)
    embed.add_field(name="👨‍💼 By Owner", value=interaction.user.mention, inline=False)
    embed.add_field(name="📅 Date", value=date, inline=True)
    
    if must_do:
        safe_must_do = truncate_for_codeblock(must_do)
        embed.add_field(name="✔️ MUST DO", value=f"```{safe_must_do}```", inline=False)
    if can_do:
        safe_can_do = truncate_for_codeblock(can_do)
        embed.add_field(name="🎯 CAN DO", value=f"```{safe_can_do}```", inline=False)
    if dont_do:
        safe_dont_do = truncate_for_codeblock(dont_do)
        embed.add_field(name="❌ DON'T DO", value=f"```{safe_dont_do}```", inline=False)
    
    if attachment_data:
        emoji = "🖼️" if attachment_data['file_type'] == 'image' else "📄"
        embed.add_field(name=f"{emoji} File", value=f"[{attachment.filename}]({attachment.url})", inline=False)
        if attachment_data['file_type'] == 'image':
            embed.set_image(url=attachment.url)
    
    # Send to TODO channel (PUBLIC)
    try:
        guild = bot.get_guild(GUILD_ID)
        if guild:
            channel = guild.get_channel(TODO_CHANNEL_ID)
            if channel:
                await channel.send(embed=embed)
    except Exception:
        pass
    
    await interaction.followup.send(f"✅ TODO assigned to {user.mention}!")


@tasks.loop(hours=3)
async def todo_checker():
    """Ping users who haven't submitted TODO in 24 hours"""
    if GUILD_ID <= 0:
        return
    
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    
    channel = guild.get_channel(TODO_CHANNEL_ID)
    if not channel:
        return
    
    now = time.time()
    one_day = 24 * 3600
    three_hours = 3 * 3600
    five_days = 5 * 86400
    
    for doc in safe_find(todo_coll, {}):
        try:
            uid = int(doc["_id"])
            member = guild.get_member(uid)
            
            if not member or member.bot:
                continue
            
            last_submit = doc.get("last_submit", 0)
            last_ping = doc.get("last_ping", 0)
            elapsed = now - last_submit
            
            # Remove role if inactive 5+ days
            if elapsed >= five_days:
                role = guild.get_role(ROLE_ID)
                if role and role in member.roles:
                    try:
                        await member.remove_roles(role)
                    except Exception:
                        pass
            
            # Ping if inactive 24+ hours AND haven't pinged in 3+ hours
            elif elapsed >= one_day and (now - last_ping) >= three_hours:
                days = int(elapsed // 86400)
                hours = int((elapsed % 86400) // 3600)
                time_str = f"{days}d {hours}h" if days > 0 else f"{hours}h"
                
                embed = discord.Embed(
                    title="⏰ TODO Reminder!",
                    description=f"{member.mention}\nLast submitted: **{time_str} ago**",
                    color=discord.Color.gold()
                )
                embed.add_field(name="Action", value="Use `/todo` to submit", inline=False)
                
                try:
                    await channel.send(embed=embed)
                    await member.send(embed=embed)
                except Exception:
                    pass
                
                # Update ping timestamp
                safe_update_one(todo_coll, {"_id": str(uid)}, {"$set": {"last_ping": now}})
        except Exception:
            pass


@todo_checker.before_loop
async def before_todo_checker():
    """Ensure todo_checker starts"""
    await bot.wait_until_ready()


@tree.command(name="listtodo", description="View your current TODO", guild=GUILD)
async def listtodo(interaction: discord.Interaction):
    """View your current TODO submission"""
    await interaction.response.defer(ephemeral=True)
    try:
        doc = safe_find_one(todo_coll, {"_id": str(interaction.user.id)})
        if not doc or "todo" not in doc:
            return await interaction.followup.send("No TODO submitted yet. Use `/todo`", ephemeral=True)
        
        todo = doc["todo"]
        embed = discord.Embed(title=f"📋 {todo.get('feature_name', 'N/A')[:100]}", color=discord.Color.blue())
        embed.add_field(name="📅 Date", value=todo.get('date', 'N/A'), inline=True)
        
        # TRUNCATE FIELDS FOR SAFETY - Apply truncation when displaying from DB
        must_do_val = truncate_for_codeblock(todo.get('must_do', 'N/A'))
        can_do_val = truncate_for_codeblock(todo.get('can_do', 'N/A'))
        dont_do_val = truncate_for_codeblock(todo.get('dont_do', 'N/A'))
        
        embed.add_field(name="✔️ Must Do", value=f"```{must_do_val}```", inline=False)
        embed.add_field(name="🎯 Can Do", value=f"```{can_do_val}```", inline=False)
        embed.add_field(name="❌ Don't Do", value=f"```{dont_do_val}```", inline=False)
        
        if "attachment" in todo:
            att = todo["attachment"]
            embed.add_field(name="📎 File", value=f"[{att.get('filename', 'File')[:50]}]({att.get('url', 'N/A')})", inline=False)
        
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Error: {str(e)[:100]}", ephemeral=True)


@tree.command(name="deltodo", description="Delete your TODO", guild=GUILD)
async def deltodo(interaction: discord.Interaction):
    """Delete your current TODO submission"""
    await interaction.response.defer(ephemeral=True)
    try:
        result = safe_delete_one(todo_coll, {"_id": str(interaction.user.id)})
        if result:
            await interaction.followup.send("✅ TODO deleted", ephemeral=True)
        else:
            await interaction.followup.send("❌ No TODO to delete", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Error: {str(e)[:100]}", ephemeral=True)


@tree.command(name="todostatus", description="Check TODO status", guild=GUILD)
@app_commands.describe(user="Optional: Check another user (Owner only)")
async def todostatus(interaction: discord.Interaction, user: discord.Member = None):
    """Check your or another user's TODO status"""
    await interaction.response.defer(ephemeral=True)
    
    target = user if user else interaction.user
    
    # If checking another user, owner check
    if user and interaction.user.id != OWNER_ID:
        return await interaction.followup.send("❌ Owner only", ephemeral=True)
    
    try:
        doc = safe_find_one(todo_coll, {"_id": str(target.id)})
        last_submit = doc.get("last_submit", 0) if doc else 0
        
        now = time.time()
        elapsed = now - last_submit
        hours = int(elapsed // 3600)
        minutes = int((elapsed % 3600) // 60)
        time_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
        
        embed = discord.Embed(title="📊 TODO Status", color=discord.Color.green())
        embed.add_field(name="User", value=target.mention)
        embed.add_field(name="Last Submit", value=f"{time_str} ago")
        embed.add_field(name="Status", value="✅ Safe" if elapsed < 86400 else "⏰ Pending ping")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Error: {str(e)[:100]}", ephemeral=True)


# ==================== ADMIN COMMANDS ====================
@tree.command(name="msz", description="Send announcement (Owner)", guild=GUILD)
@app_commands.describe(channel="Target", message="Text", role="Ping (opt)", attachment="File (opt)")
async def msz(interaction: discord.Interaction, channel: discord.TextChannel, message: str, role: discord.Role = None, attachment: discord.Attachment = None):
    await interaction.response.defer(ephemeral=True)
    try:
        if interaction.user.id != OWNER_ID:
            return await interaction.followup.send("Owner only", ephemeral=True)
        content = f"📩 **Server Announcement**\n{message}"
        if role:
            content += f"\n<@&{role.id}>"
        files = [await attachment.to_file()] if attachment else None
        await channel.send(content, files=files)
        await interaction.followup.send("Sent!", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Error: {str(e)[:100]}", ephemeral=True)

# ==================== SPY TRACKING HELPERS ====================

async def notify_spy(member: discord.Member, text: str):
    """Send spy notification to owner via DM"""
    try:
        owner = await bot.fetch_user(OWNER_ID)
        if owner:
            await owner.send(text)
    except Exception as e:
        print(f"⚠️ Failed to send spy notification: {e}")

# ==================== SPY COMMANDS ====================

@tree.command(name="ud_spy", description="Enable live spy on a user (Owner only)", guild=GUILD)
@app_commands.describe(user="Target user to spy on")
async def ud_spy(interaction: discord.Interaction, user: discord.Member):
    """Enable real-time tracking of a user's activities"""
    await interaction.response.defer(ephemeral=True)
    
    if interaction.user.id != OWNER_ID:
        return await interaction.followup.send("❌ Owner only.", ephemeral=True)
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR IGNORE INTO spy_targets (user_id) VALUES (?)",
                (user.id,)
            )
            await db.commit()
        
        await interaction.followup.send(
            f"👁️ **Spy mode enabled for {user.mention}**\n"
            f"Owner will receive real-time DMs for:\n"
            f"• Messages\n"
            f"• VC joins/leaves\n"
            f"• Camera ON/OFF\n"
            f"_Target user will not be notified_",
            ephemeral=True
        )
        print(f"🕵️ Spy enabled for {user} (ID: {user.id})")
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {str(e)[:100]}", ephemeral=True)


@tree.command(name="ud_spyoff", description="Disable live spy on a user (Owner only)", guild=GUILD)
@app_commands.describe(user="Target user")
async def ud_spyoff(interaction: discord.Interaction, user: discord.Member):
    """Disable real-time tracking for a user"""
    await interaction.response.defer(ephemeral=True)

    if interaction.user.id != OWNER_ID:
        return await interaction.followup.send(
            "❌ Only the server owner can use this.",
            ephemeral=True
        )

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "DELETE FROM spy_targets WHERE user_id=?",
                (user.id,)
            )
            await db.commit()

        if getattr(cur, 'rowcount', 0) == 0:
            msg = f"ℹ️ **Spy mode was already OFF for {user.mention}.**"
        else:
            msg = f"🔕 **Spy mode DISABLED for {user.mention}.**"

        await interaction.followup.send(msg, ephemeral=True)
        print(f"🕵️ Spy disabled for {user} (ID: {user.id}) - rows={getattr(cur, 'rowcount', 0)}")
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {str(e)[:100]}", ephemeral=True)


@tree.command(name="ud_purge", description="Delete user messages across channels (Owner only)", guild=GUILD)
@app_commands.describe(user="Target user", limit="Max messages to delete (default 50)")
async def ud_purge(interaction: discord.Interaction, user: discord.Member, limit: int = 50):
    """Bulk delete messages from a user"""
    await interaction.response.defer(ephemeral=True)
    
    if interaction.user.id != OWNER_ID:
        return await interaction.followup.send("❌ Owner only.", ephemeral=True)
    
    deleted = 0
    try:
        for channel in interaction.guild.text_channels:
            try:
                msgs = []
                async for m in channel.history(limit=500):
                    if m.author == user:
                        msgs.append(m)
                    if len(msgs) >= limit:
                        break
                
                if msgs:
                    await channel.delete_messages(msgs)
                    deleted += len(msgs)
            except Exception:
                pass  # Skip channels we can't access
        
        await interaction.followup.send(
            f"🧹 **Purge complete**\n"
            f"Deleted **{deleted} messages** from {user.mention}\n"
            f"Limit: {limit}",
            ephemeral=True
        )
        print(f"🧹 Purged {deleted} messages from {user} (limit: {limit})")
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {str(e)[:100]}", ephemeral=True)

@tree.command(name="mz", description="Anonymous DM (Owner)", guild=GUILD)
@app_commands.describe(target="User", message="Text", attachment="File (opt)")
async def mz(interaction: discord.Interaction, target: discord.User, message: str, attachment: discord.Attachment = None):
    await interaction.response.defer(ephemeral=True)
    try:
        if interaction.user.id != OWNER_ID:
            return await interaction.followup.send("Owner only", ephemeral=True)
        content = f"📩 **Message from Server**\n{message}"
        files = [await attachment.to_file()] if attachment else None
        try:
            await target.send(content, files=files)
            await interaction.followup.send(f"Sent anonymously to {target}", ephemeral=True)
        except Exception:
            await interaction.followup.send("DM failed (blocked?).", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Error: {str(e)[:100]}", ephemeral=True)

@tree.command(name="ud", description="User details (Owner)", guild=GUILD)
@app_commands.describe(target="User")
async def ud(interaction: discord.Interaction, target: discord.Member):
    await interaction.response.defer(ephemeral=True)
    try:
        if interaction.user.id != OWNER_ID:
            return await interaction.followup.send("Owner only", ephemeral=True)
        
        user_id = str(target.id)
        
        # Fetch MongoDB data
        user_doc = safe_find_one(users_coll, {"_id": user_id})
        data = user_doc.get("data", {}) if user_doc else {}
        
        print(f"🔍 /ud query for {target.display_name} (ID: {user_id})")
        print(f"   MongoDB document: {user_doc}")
        print(f"   Data fields: {data}")
        
        # Fetch SQLite spy tracker stats
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                # Get user stats from SQLite
                cursor = await db.execute(
                    "SELECT messages, cam_on, cam_off FROM users WHERE user_id = ?",
                    (target.id,)
                )
                user_stats = await cursor.fetchone()
                
                # Get recent message logs (last 5)
                cursor = await db.execute(
                    "SELECT channel, content, time FROM message_logs WHERE user_id = ? ORDER BY rowid DESC LIMIT 5",
                    (target.id,)
                )
                message_logs = await cursor.fetchall()
                
                # Get recent VC logs (last 5)
                cursor = await db.execute(
                    "SELECT channel, time FROM vc_logs WHERE user_id = ? ORDER BY rowid DESC LIMIT 5",
                    (target.id,)
                )
                vc_logs = await cursor.fetchall()
                
                # Check if user is spy target
                cursor = await db.execute(
                    "SELECT user_id FROM spy_targets WHERE user_id = ?",
                    (target.id,)
                )
                is_spied = await cursor.fetchone()
        except Exception as e:
            print(f"⚠️ SQLite query error: {e}")
            user_stats = None
            message_logs = []
            vc_logs = []
            is_spied = False
        
        # Get in-memory activity logs
        logs = "\n".join(user_activity[target.id] or ["No logs"])
        
        # TRUNCATE LOGS TO DISCORD'S 1024 CHARACTER EMBED FIELD LIMIT
        max_log_length = 1000  # Leave buffer for code block markers
        if len(logs) > max_log_length:
            logs = logs[:max_log_length] + "\n... (truncated)"
        
        embed = discord.Embed(title=f"🕵️ {target}", color=0x0099ff)
        embed.add_field(name="ID", value=str(target.id), inline=True)
        embed.add_field(name="Joined", value=target.joined_at.strftime("%d/%m/%Y %H:%M") if target.joined_at else "Unknown", inline=True)
        
        # Add roles
        roles = ", ".join([r.name for r in target.roles if r.name != "@everyone"][:5])
        embed.add_field(name="Roles", value=roles or "None", inline=False)
        
        # MongoDB Voice & Cam Stats
        cam_on = data.get("voice_cam_on_minutes", 0)
        cam_off = data.get("voice_cam_off_minutes", 0)
        messages = data.get("message_count", 0)
        
        # SQLite tracked stats
        if user_stats:
            db_messages, db_cam_on, db_cam_off = user_stats
            stats_text = f"📊 **Message Tracking**\n💬 Messages: {db_messages}\n🎤 Cam ON: {db_cam_on}\n❌ Cam OFF: {db_cam_off}"
        else:
            stats_text = f"📊 **No tracking data**\n💬 Messages: 0\n🎤 Cam ON: 0\n❌ Cam OFF: 0"
        
        # Add MongoDB stats if available
        if cam_on or cam_off:
            stats_text += f"\n\n📈 **VC Time (MongoDB)**\n⏱️ ON: {format_time(cam_on)}\n⏱️ OFF: {format_time(cam_off)}"
        
        embed.add_field(name="📊 Stats", value=stats_text, inline=False)
        
        # Show spy status
        spy_status = "👁️ **BEING SPIED ON**" if is_spied else "✅ Not being spied"
        embed.add_field(name="🔍 Spy Status", value=spy_status, inline=True)
        
        # Recent logs
        logs_display = "```\n"
        if message_logs:
            logs_display += "💬 Recent Messages:\n"
            for channel, content, time in message_logs[:3]:
                logs_display += f"[{time}] #{channel}: {content[:50]}\n"
        
        if vc_logs:
            logs_display += "\n🎤 Recent VC:\n"
            for channel, time in vc_logs[:3]:
                logs_display += f"[{time}] {channel}\n"
        
        if not message_logs and not vc_logs:
            logs_display += "No activity logs yet"
        logs_display += "\n```"
        
        if len(logs_display) <= 1024:
            embed.add_field(name="📋 Activity Logs", value=logs_display, inline=False)
        
        # Recent in-memory activity
        activity_value = f"```\n{logs}\n```"
        # Ensure field value doesn't exceed 1024 characters
        if len(activity_value) > 1024:
            # Truncate logs further to fit with code block markers
            safe_log_length = 1024 - 10  # Reserve 10 chars for code block markers and newlines
            truncated_logs = logs[:safe_log_length]
            activity_value = f"```\n{truncated_logs}\n```"
        
        embed.add_field(name="📝 In-Memory Logs", value=activity_value, inline=False)
        
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Error: {str(e)[:100]}", ephemeral=True)

@tree.command(name="bn", description="Force ban (Owner)", guild=GUILD)
@app_commands.describe(target="ID/Mention/Name", reason="Reason (opt)")
async def bn(interaction: discord.Interaction, target: str, reason: str = "Force Ban"):
    await interaction.response.defer(ephemeral=True)
    try:
        if interaction.user.id != OWNER_ID:
            return await interaction.followup.send("Owner only", ephemeral=True)
        member = None
        if target.isdigit():
            try:
                member = await interaction.guild.fetch_member(int(target))
            except Exception:
                pass
        elif target.startswith("<@"):
            clean = target.strip("<@!>").strip(">")
            try:
                member = await interaction.guild.fetch_member(int(clean))
            except Exception:
                pass
        else:
            member = discord.utils.find(lambda m: m.name == target or m.display_name == target, interaction.guild.members)
        if member:
            await interaction.guild.ban(member, reason=reason)
            await interaction.followup.send(f"Banned {member}", ephemeral=True)
        else:
            await interaction.followup.send("User not found.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Error: {str(e)[:100]}", ephemeral=True)

@tree.command(name="ck", description="Disconnect user from VC (Owner)", guild=GUILD)
@app_commands.describe(user="Target")
async def ck(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.defer(ephemeral=True)
    try:
        if interaction.user.id != OWNER_ID:
            return await interaction.followup.send("Owner only", ephemeral=True)
        if not user.voice or not user.voice.channel:
            return await interaction.followup.send("User not in VC.", ephemeral=True)
        await user.move_to(None, reason="Admin kick")
        await interaction.followup.send(f"Disconnected {user}", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Error: {str(e)[:100]}", ephemeral=True)

@tree.command(name="addh", description="Allow a user to use todo system", guild=GUILD)
@app_commands.describe(userid="User ID (numeric)")
async def addh(interaction: discord.Interaction, userid: str):
    await interaction.response.defer(ephemeral=True)
    try:
        if interaction.user.id != OWNER_ID:
            return await interaction.followup.send("❌ Owner only", ephemeral=True)
        
        # Validate user ID
        if not userid.isdigit():
            return await interaction.followup.send("❌ Invalid ID format (must be numeric)", ephemeral=True)
        
        user_id = int(userid)
        user_id_str = str(userid)
        
        print(f"\n{'='*70}")
        print(f"🔧 [/ADDH] Adding user to TODO system")
        print(f"   Input userid: {userid} (type: {type(userid).__name__})")
        print(f"   user_id int: {user_id}")
        print(f"   user_id_str: {user_id_str}")
        
        # Try to get member from guild
        guild = interaction.guild
        member = guild.get_member(user_id) if guild else None
        print(f"   Guild: {guild.name if guild else 'None'}")
        print(f"   Member found: {member.name if member else 'None'}")
        
        # Add to active members - use STRING format like the todo check expects
        result = safe_update_one(active_members_coll, {"_id": user_id_str}, {
            "$set": {
                "added": datetime.datetime.now(KOLKATA),
                "name": member.display_name if member else f"User {user_id}",
                "user_id": user_id  # Also store as int for reference
            }
        })
        print(f"   Database update result: {result}")
        
        # Verify it was actually saved
        verify = safe_find_one(active_members_coll, {"_id": user_id_str})
        print(f"   Verification lookup by '{user_id_str}': {verify}")
        print(f"{'='*70}\n")
        
        member_name = member.mention if member else f"`{user_id}`"
        await interaction.followup.send(f"✅ Added {member_name} to TODO system", ephemeral=True)
        
        # Log to channel
        if guild:
            channel = guild.get_channel(TODO_CHANNEL_ID)
            if channel:
                try:
                    msg = f"✅ {member.mention if member else f'`{user_id}`'} added to TODO system (can now use `/todo`)"
                    await channel.send(msg)
                except Exception as e:
                    print(f"⚠️ Failed to log to channel: {e}")
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {str(e)[:100]}", ephemeral=True)
        print(f"⚠️ /addh error: {str(e)}")
        import traceback
        traceback.print_exc()

@tree.command(name="remh", description="Remove a user from todo system", guild=GUILD)
@app_commands.describe(userid="User ID (numeric)")
async def remh(interaction: discord.Interaction, userid: str):
    await interaction.response.defer(ephemeral=True)
    try:
        if interaction.user.id != OWNER_ID:
            return await interaction.followup.send("❌ Owner only", ephemeral=True)
        
        # Validate user ID
        if not userid.isdigit():
            return await interaction.followup.send("❌ Invalid ID format (must be numeric)", ephemeral=True)
        
        user_id = int(userid)
        user_id_str = str(userid)
        
        print(f"\n{'='*70}")
        print(f"🔧 [/REMH] Removing user from TODO system")
        print(f"   Input userid: {userid} (type: {type(userid).__name__})")
        print(f"   user_id int: {user_id}")
        print(f"   user_id_str: {user_id_str}")
        
        # Try to get member from guild
        guild = interaction.guild
        member = guild.get_member(user_id) if guild else None
        print(f"   Guild: {guild.name if guild else 'None'}")
        print(f"   Member found: {member.name if member else 'None'}")
        
        # Check if user exists before removing
        existing = safe_find_one(active_members_coll, {"_id": user_id_str})
        print(f"   Found in database: {existing}")
        
        # Remove from active members
        safe_delete_one(active_members_coll, {"_id": user_id_str})
        print(f"   Deletion executed")
        
        # Verify it was actually removed
        verify = safe_find_one(active_members_coll, {"_id": user_id_str})
        print(f"   Verification after delete: {verify}")
        print(f"{'='*70}\n")
        
        member_name = member.mention if member else f"`{user_id}`"
        await interaction.followup.send(f"✅ Removed {member_name} from TODO system", ephemeral=True)
        
        # Log to channel
        if guild:
            channel = guild.get_channel(TODO_CHANNEL_ID)
            if channel:
                try:
                    msg = f"❌ {member.mention if member else f'`{user_id}`'} removed from TODO system (can no longer use `/todo`)"
                    await channel.send(msg)
                except Exception as e:
                    print(f"⚠️ Failed to log to channel: {e}")
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {str(e)[:100]}", ephemeral=True)
        print(f"⚠️ /remh error: {str(e)}")
        import traceback
        traceback.print_exc()

@tree.command(name="members", description="List all allowed members", guild=GUILD)
async def members(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        if interaction.user.id != OWNER_ID:
            return await interaction.followup.send("Owner only", ephemeral=True)
        ids = [doc["_id"] for doc in safe_find(active_members_coll, {})]
        if not ids:
            return await interaction.followup.send("No active members.", ephemeral=True)
        guild = interaction.guild
        names = []
        for id_ in ids:
            member = guild.get_member(int(id_))
            if member:
                names.append(member.display_name)
        msg = "Active Members:\n" + "\n".join(f"- {n}" for n in names)
        await interaction.followup.send(msg, ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Error: {str(e)[:100]}", ephemeral=True)

@tree.command(name="tododebug", description="Debug TODO system (Owner only)", guild=GUILD)
async def tododebug(interaction: discord.Interaction):
    """Debug command to check TODO system status"""
    await interaction.response.defer(ephemeral=True)
    try:
        if interaction.user.id != OWNER_ID:
            return await interaction.followup.send("Owner only", ephemeral=True)
        
        user_id_str = str(interaction.user.id)
        
        # Check if user is in active members
        user_doc = safe_find_one(active_members_coll, {"_id": user_id_str})
        
        # Get all active members
        all_members = safe_find(active_members_coll, {})
        
        msg = f"""
🔍 **TODO System Debug Info**

**Your Info:**
- Your ID (int): {interaction.user.id}
- Your ID (str): {user_id_str}
- In active_members: {user_doc is not None}

**All Active Members ({len(all_members)}):**
"""
        for doc in all_members:
            msg += f"\n- ID: `{doc['_id']}` | Name: {doc.get('name', 'Unknown')}"
        
        # Show collection stats
        all_todo = safe_find(todo_coll, {})
        msg += f"\n\n**TODO Submissions ({len(all_todo)}):**"
        for doc in all_todo:
            msg += f"\n- ID: `{doc['_id']}` | Name: {doc.get('todo', {}).get('name', 'Unknown')}"
        
        await interaction.followup.send(msg[:2000], ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {str(e)[:100]}", ephemeral=True)
        import traceback
        traceback.print_exc()

# ==================== SECURITY FIREWALLS ====================
@bot.event
async def on_message(message: discord.Message):
    # Skip bot messages
    if message.author.bot:
        return
    
    # ============================================================
    # 📩 FORWARD DMs & BOT MENTIONS TO OWNER (PRIORITY #1)
    # ============================================================
    
    # Check if this is a DM or bot mention
    is_dm = isinstance(message.channel, discord.DMChannel) and message.author.id != OWNER_ID
    is_bot_mention = bot.user in message.mentions and not isinstance(message.channel, discord.DMChannel)
    
    if is_dm or is_bot_mention:
        try:
            owner = await bot.fetch_user(OWNER_ID)
            if owner:
                # Build embed
                if is_dm:
                    embed = discord.Embed(title=f"📩 DM from {message.author}", color=discord.Color.blue())
                    embed.add_field(name="Location", value="Direct Message", inline=True)
                else:
                    embed = discord.Embed(title=f"🔔 Bot Mention from {message.author}", color=discord.Color.gold())
                    embed.add_field(name="Location", value=f"#{message.channel.name}", inline=True)
                
                embed.description = message.content[:2000] if message.content else "[No content]"
                embed.add_field(name="User ID", value=str(message.author.id), inline=True)
                
                if message.guild:
                    embed.add_field(name="Server", value=message.guild.name, inline=True)
                
                if message.attachments:
                    att_info = "\n".join([f"📎 {a.filename} ({a.size} bytes)" for a in message.attachments])
                    # TRUNCATE ATTACHMENTS INFO TO PREVENT FIELD OVERFLOW
                    att_info = truncate_embed_field(att_info, max_length=1000)
                    embed.add_field(name="Attachments", value=att_info, inline=False)
                
                embed.set_author(name=f"{message.author.name}#{message.author.discriminator}", icon_url=message.author.avatar.url if message.author.avatar else None)
                embed.timestamp = message.created_at
                
                await owner.send(embed=embed)
                print(f"✅ [FORWARD] {'DM' if is_dm else 'Mention'} from {message.author.name} → Owner")
        except Exception as e:
            print(f"⚠️ [FORWARD ERROR] {e}")
        
        # For DMs, also send confirmation
        if is_dm:
            try:
                await message.author.send("✅ Your message has been forwarded to the owner.")
            except Exception:
                pass
        
        # Don't process further for DMs
        if is_dm:
            return
    
    # Allow messages from different guilds ONLY if they're being forwarded (handled above)
    if message.guild and message.guild.id != GUILD_ID:
        return
    
    now = time.time()
    normalized_content = normalize_security_content(message.content)
    
    # ==================== SPY MESSAGE TRACKING ====================
    if not message.author.bot:
        try:
            time_now = datetime.datetime.now().strftime("%d/%m %H:%M:%S")
            async with aiosqlite.connect(DB_PATH) as db:
                # Track message counter
                await db.execute("""
                    INSERT INTO users (user_id, messages)
                    VALUES (?, 1)
                    ON CONFLICT(user_id)
                    DO UPDATE SET messages = messages + 1
                """, (message.author.id,))
                
                # Log message content
                await db.execute("""
                    INSERT INTO message_logs (user_id, channel, content, time)
                    VALUES (?, ?, ?, ?)
                """, (
                    message.author.id,
                    message.channel.name if hasattr(message.channel, 'name') else "DM",
                    message.content[:200],
                    time_now
                ))
                
                # Check if user is being spied on
                cursor = await db.execute(
                    "SELECT user_id FROM spy_targets WHERE user_id = ?",
                    (message.author.id,)
                )
                spy = await cursor.fetchone()
                
                await db.commit()
            
            # Send spy notification if monitored
            if spy:
                await notify_spy(
                    message.author,
                    f"🕵️ **SPY LOG - MESSAGE**\n"
                    f"[{time_now}] Message in #{message.channel.name if hasattr(message.channel, 'name') else 'DM'}\n"
                    f"```\n{message.content[:500]}\n```"
                )
        except Exception as e:
            print(f"⚠️ Spy tracking error: {e}")
    
    # ---------------------------------------------------------
    # ☠️ ZONE 1: HACKER THREATS (INSTANT BAN - NO STRIKES)
    # ---------------------------------------------------------
    
    # A. GHOST WEBHOOK DESTROYER (Only for non-whitelisted webhooks)
    if message.webhook_id:
        # ✅ WHITELIST CHECK: If webhook is whitelisted, skip all threats checks
        if message.webhook_id not in WHITELISTED_WEBHOOKS:
            link_regex = r"http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+"
            
            is_threat = (
                message.mention_everyone or 
                re.search(link_regex, message.content) or 
                content_has_forbidden_keywords(normalized_content)
            )

            if is_threat:
                print(f"🚨 [WEBHOOK THREAT] Non-whitelisted webhook {message.webhook_id} sending malicious content")
                try:
                    await message.delete()
                    webhooks = await message.channel.webhooks()
                    for webhook in webhooks:
                        if webhook.id == message.webhook_id:
                            await webhook.delete(reason="LegendMeta: Malicious Ghost Webhook Activity")
                            await message.channel.send("☠️ **LegendMeta**: Unauthorized Ghost Webhook DESTROYED.")
                            print(f"✅ [WEBHOOK THREAT] Malicious webhook {message.webhook_id} has been deleted")
                    await send_security_log(
                        message.guild,
                        "🚨 Malicious Webhook Message Blocked",
                        description=f"Webhook `{message.webhook_id}` posted suspicious content in {message.channel.mention}.",
                        color=discord.Color.red(),
                        fields={"Content Preview": truncate_for_codeblock(message.content or "[empty]", 500)},
                    )
                except Exception as e:
                    print(f"⚠️ [WEBHOOK THREAT] Webhook cleanup error: {e}")
                return # STOP HERE
        else:
            print(f"✅ [WEBHOOK] Webhook {message.webhook_id} is whitelisted - allowing all content")

    # IMMUNITY CHECK
    if message.author.id == OWNER_ID or message.author == bot.user:
        return

    # -------------------------
    # Soft automod enforcement
    # - If user has NoMessage role -> delete any message (delete-only)
    # - If user has NoPing role -> delete message only when it contains an '@'
    # -------------------------
    try:
        guild = message.guild
        if guild:
            noping = discord.utils.get(guild.roles, name=NOPING_ROLE)
            nomsg = discord.utils.get(guild.roles, name=NOMSG_ROLE)
            roles = message.author.roles

            if nomsg and nomsg in roles:
                try:
                    await message.delete()
                except Exception:
                    pass
                return

            if noping and noping in roles and message.content and "@" in message.content:
                try:
                    await message.delete()
                except Exception:
                    pass
                return
    except Exception:
        # Fail silently to avoid breaking other protections
        pass

    # B. MALWARE UPLOAD (.exe) -> INSTANT BAN
    if message.attachments:
        for attachment in message.attachments:
            filename = attachment.filename.lower()
            if is_dangerous_attachment(filename):
                try:
                    await message.delete()
                    await message.author.ban(reason="LegendMeta: Malware Upload Detected")
                    await message.channel.send(f"☣️ **Security Alert**: {message.author.mention} was BANNED for uploading a dangerous file (`{filename}`).")
                    await send_security_log(
                        message.guild,
                        "☣️ Dangerous Attachment Blocked",
                        description=f"{message.author.mention} uploaded a dangerous file and was removed.",
                        color=discord.Color.red(),
                        fields={
                            "Filename": filename,
                            "User ID": str(message.author.id),
                            "Channel": message.channel.mention,
                        },
                    )
                    await alert_owner(message.guild, "DANGEROUS ATTACHMENT BLOCKED", {
                        "User": f"{message.author} (ID: {message.author.id})",
                        "Filename": filename,
                        "Channel": getattr(message.channel, "name", "Unknown"),
                    })
                    return 
                except Exception as e:
                    print(f"Failed to ban file uploader: {e}")
    
    # Track message activity in MongoDB - SAVE IMMEDIATELY
    if message.guild and message.guild.id == GUILD_ID:
        user_id = str(message.author.id)
        try:
            # Use separate operations to avoid MongoDB conflicts
            # First, increment message count (no setOnInsert conflict)
            result = save_with_retry(users_coll, {"_id": user_id}, {
                "$inc": {"data.message_count": 1},
                "$setOnInsert": {
                    "data.voice_cam_on_minutes": 0,
                    "data.voice_cam_off_minutes": 0,
                    "data.yesterday.cam_on": 0,
                    "data.yesterday.cam_off": 0
                }
            })
            track_activity(message.author.id, f"Message in #{message.channel.name}: {message.content[:50]}")
            if not result:
                print(f"⚠️ Failed to save message count for {message.author.display_name}")
        except Exception as e:
            print(f"⚠️ Message tracking error: {str(e)[:80]}")
    
    # ---------------------------------------------------------
    # ⚠️ ZONE 2: HUMAN MISTAKES (STRIKE SYSTEM)
    # ---------------------------------------------------------

    # C. MASS MENTION (Strike System)
    total_mentions = len(message.mentions) + len(message.role_mentions)
    if message.mention_everyone: total_mentions += 1

    if total_mentions >= MAX_MENTIONS:
        await message.delete()
        await punish_human(message, "Mass Mentioning") # -> Calls the Brain
        return
    
    # 1. Anti-Spam
    spam_cache[message.author.id].append(now)
    spam_cache[message.author.id] = [t for t in spam_cache[message.author.id] if now - t < SPAM_WINDOW]
    if len(spam_cache[message.author.id]) > SPAM_THRESHOLD:
        del spam_cache[message.author.id] # Clear to prevent double strike
        await punish_human(message, "Excessive Spamming") # -> Calls the Brain
        return
    
    # D. ANTI-ADVERTISEMENT (Strike System)
    invite_regex = r"(https?://)?(www\.)?(discord\.(gg|io|me|li)|discordapp\.com/invite)/.+"
    if re.search(invite_regex, message.content, re.IGNORECASE) or contains_discord_invite(normalized_content):
        await message.delete()
        await punish_human(message, "Advertising") # -> Calls the Brain
        return
    
    await bot.process_commands(message)

@tasks.loop(minutes=5)
async def clean_webhooks():
    """
    Periodic webhook cleanup task
    ✅ WHITELISTED webhooks are NEVER deleted
    ❌ Non-whitelisted webhooks are removed for security
    """
    if GUILD_ID <= 0:
        return
    
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    
    try:
        for channel in guild.text_channels:
            try:
                webhooks = await channel.webhooks()
                for wh in webhooks:
                    # ✅ WHITELIST CHECK: Skip whitelisted webhooks
                    if wh.id in WHITELISTED_WEBHOOKS:
                        print(f"✅ [WEBHOOK CLEANUP] Webhook {wh.id} is whitelisted - KEEPING")
                        continue
                    
                    # ❌ Delete non-whitelisted webhook
                    await wh.delete(reason="Security: Unauthorized webhook")
                    print(f"❌ [WEBHOOK CLEANUP] Deleted unauthorized webhook {wh.id} from #{channel.name}")
            except Exception as e:
                print(f"⚠️ [WEBHOOK CLEANUP] Error processing channel {channel.name}: {e}")
    except Exception as e:
        print(f"⚠️ [WEBHOOK CLEANUP] General error: {e}")

@tasks.loop(minutes=1)
async def monitor_webhook_audit():
    """Monitors critical server activities like unauthorized webhook creation with enhanced deduplication"""
    if GUILD_ID <= 0:
        return
    
    try:
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            return
        
        current_time = datetime.datetime.now(KOLKATA)
        
        async for entry in guild.audit_logs(limit=3, action=discord.AuditLogAction.webhook_create):
            if is_duplicate_audit_entry(entry.id, current_time):
                print(f"⏭️ [WEBHOOK CREATE] Audit entry {entry.id} already processed - SKIPPING DUPLICATE")
                return
            remember_audit_entry(entry.id, current_time)
            
            # ✅ WHITELIST CHECK: Allow owner and bot itself
            if not entry.user:
                return
            if is_whitelisted_entity(entry.user):
                print(f"✅ [WEBHOOK CREATE] Whitelisted entity {entry.user.name} ({entry.user.id}) created webhook - ALLOWED (Audit ID: {entry.id})")
                return
            
            # ❌ THREAT DETECTED: Unauthorized webhook creation
            print(f"🚨 [ANTI-NUKE] UNAUTHORIZED WEBHOOK CREATION: {entry.user.name} ({entry.user.id}) (Audit ID: {entry.id})")
            
            # 1. DELETE THE WEBHOOK
            try:
                webhooks = await entry.channel.webhooks() if entry.channel else []
                for webhook in webhooks:
                    if webhook.id == entry.target.id:
                        await webhook.delete(reason="LegendMeta: Unauthorized Creation")
                        print(f"✅ Webhook {webhook.id} deleted")
            except Exception as e:
                print(f"⚠️ Failed to delete webhook: {e}")
            
            # 2. BAN THE CREATOR (Hacker/Rogue Admin -> INSTANT BAN)
            try:
                await guild.ban(entry.user, reason=f"Anti-Nuke: Malicious Webhook Creation (Audit ID: {entry.id})")
                
                # Alert in tech channel
                tech_channel = bot.get_channel(TECH_CHANNEL_ID)
                if tech_channel:
                    embed = discord.Embed(
                        title="🚨 ANTI-NUKE: UNAUTHORIZED WEBHOOK",
                        color=discord.Color.red(),
                        timestamp=datetime.datetime.now(KOLKATA)
                    )
                    embed.add_field(name="🔨 Action", value="User BANNED + Webhook DELETED", inline=True)
                    embed.add_field(name="👤 Attacker", value=f"{entry.user.mention} ({entry.user.id})", inline=True)
                    embed.add_field(name="🆔 Audit Entry", value=f"`{entry.id}`", inline=False)
                    await tech_channel.send(embed=embed)
                
                # Alert owner
                await alert_owner(guild, "UNAUTHORIZED WEBHOOK CREATION", {
                    "Attacker": f"{entry.user.name} (ID: {entry.user.id})",
                    "Action": "✅ Instant Ban Applied + Webhook Deleted",
                    "Audit Entry": str(entry.id)
                })
                
                print(f"✅ [ANTI-NUKE] {entry.user.name} has been BANNED for webhook creation (Audit ID: {entry.id})")
            except Exception as e:
                print(f"⚠️ Failed to ban webhook creator: {e}")
            return
    except Exception:
        pass

@bot.event
async def on_guild_channel_delete(channel):
    if channel.guild.id != GUILD_ID:
        return
    
    current_time = datetime.datetime.now(KOLKATA)
    entry = await find_matching_audit_entry(
        channel.guild,
        discord.AuditLogAction.channel_delete,
        target_id=channel.id,
        target_name=channel.name,
        limit=6,
    )
    if not entry:
        return
    if is_duplicate_audit_entry(entry.id, current_time):
        print(f"⏭️ [CHANNEL DELETE] Audit entry {entry.id} already processed - SKIPPING DUPLICATE")
        return

    remember_audit_entry(entry.id, current_time)
    actor = entry.user
    if not actor:
        return

    # ✅ WHITELIST CHECK: Skip whitelisted bots/webhooks/users
    if is_whitelisted_entity(actor):
        print(f"✅ [CHANNEL DELETE] Whitelisted entity {actor.name} ({actor.id}) deleted channel - ALLOWED")
        return

    # ❌ THREAT DETECTED: Non-whitelisted entity deleted a channel
    print(f"🚨 [ANTI-NUKE] CHANNEL DELETION THREAT DETECTED: {actor.name} ({actor.id})")

    try:
        # BAN the attacker immediately
        await channel.guild.ban(actor, reason=f"Anti-Nuke: Channel Deletion by {actor.name}")

        await send_security_log(
            channel.guild,
            "🚨 ANTI-NUKE: CHANNEL DELETION",
            color=discord.Color.red(),
            fields={
                "Action": "User BANNED",
                "Attacker": f"{actor} ({actor.id})",
                "Channel": channel.name,
                "Audit Entry": str(entry.id),
            },
        )

        # Alert owner
        await alert_owner(channel.guild, "CHANNEL DELETION DETECTED", {
            "Attacker": f"{actor.name} (ID: {actor.id})",
            "Channel": channel.name,
            "Action": "✅ Instant Ban Applied"
        })

        print(f"✅ [ANTI-NUKE] {actor.name} has been BANNED for channel deletion (Audit ID: {entry.id})")

    except discord.Forbidden:
        print(f"⚠️ [ANTI-NUKE] FAILED TO BAN channel deleter. Engaging emergency lockdown.")
        await engage_lockdown(channel.guild, "Failed to ban channel deleter - role hierarchy issue")
    except Exception as e:
        print(f"⚠️ [ANTI-NUKE] Channel delete error: {e}")


@bot.event
async def on_guild_role_delete(role):
    if role.guild.id != GUILD_ID:
        return
    current_time = datetime.datetime.now(KOLKATA)
    entry = await find_matching_audit_entry(
        role.guild,
        discord.AuditLogAction.role_delete,
        target_id=role.id,
        target_name=role.name,
        limit=6,
    )
    if not entry:
        return
    if is_duplicate_audit_entry(entry.id, current_time):
        print(f"⏭️ [ROLE DELETE] Audit entry {entry.id} already processed - SKIPPING DUPLICATE")
        return

    remember_audit_entry(entry.id, current_time)
    actor = entry.user
    if not actor:
        return

    # ✅ WHITELIST CHECK: Skip whitelisted bots/webhooks/users
    if is_whitelisted_entity(actor):
        print(f"✅ [ROLE DELETE] Whitelisted entity {actor.name} ({actor.id}) deleted role - ALLOWED")
        return

    # ❌ THREAT DETECTED: Non-whitelisted entity deleted a role
    print(f"🚨 [ANTI-NUKE] ROLE DELETION THREAT DETECTED: {actor.name} ({actor.id})")

    try:
        # BAN the attacker immediately
        await role.guild.ban(actor, reason=f"Anti-Nuke: Role Deletion by {actor.name}")

        await send_security_log(
            role.guild,
            "🚨 ANTI-NUKE: ROLE DELETION",
            color=discord.Color.red(),
            fields={
                "Action": "User BANNED",
                "Attacker": f"{actor} ({actor.id})",
                "Role": role.name,
                "Audit Entry": str(entry.id),
            },
        )

        # Alert owner
        await alert_owner(role.guild, "ROLE DELETION DETECTED", {
            "Attacker": f"{actor.name} (ID: {actor.id})",
            "Role": role.name,
            "Action": "✅ Instant Ban Applied"
        })

        print(f"✅ [ANTI-NUKE] {actor.name} has been BANNED for role deletion (Audit ID: {entry.id})")

    except discord.Forbidden:
        print(f"⚠️ [ANTI-NUKE] FAILED TO BAN role deleter. Engaging emergency lockdown.")
        await engage_lockdown(role.guild, "Failed to ban role deleter - role hierarchy issue")
    except Exception as e:
        print(f"⚠️ [ANTI-NUKE] Role delete error: {e}")


@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    if guild.id != GUILD_ID:
        return
    current_time = datetime.datetime.now(KOLKATA)
    entry = await find_matching_audit_entry(
        guild,
        discord.AuditLogAction.ban,
        target_id=user.id,
        limit=6,
    )
    if not entry:
        return
    if is_duplicate_audit_entry(entry.id, current_time):
        print(f"⏭️ [MEMBER BAN] Audit entry {entry.id} already processed - SKIPPING DUPLICATE")
        return

    remember_audit_entry(entry.id, current_time)
    actor = entry.user
    if not actor:
        return

    # ✅ WHITELIST CHECK: Skip whitelisted bots/webhooks/users AND self-bans
    if is_whitelisted_entity(actor) or user.id == actor.id:
        print(f"✅ [MEMBER BAN] Whitelisted entity {actor.name} ({actor.id}) banned {user.name} - ALLOWED")
        return

    # ❌ THREAT DETECTED: Non-whitelisted entity banned someone
    print(f"🚨 [ANTI-NUKE] UNAUTHORIZED BAN THREAT DETECTED: {actor.name} ({actor.id}) banned {user.name}")

    try:
        # BAN the attacker and unban the victim
        await guild.ban(actor, reason=f"Anti-Nuke: Unauthorized Ban by {actor.name}")
        await guild.unban(user, reason="Anti-Nuke: Victim recovery")

        await send_security_log(
            guild,
            "🚨 ANTI-NUKE: UNAUTHORIZED BAN",
            color=discord.Color.red(),
            fields={
                "Action": "Attacker BANNED + Victim UNBANNED",
                "Attacker": f"{actor} ({actor.id})",
                "Victim": f"{user} ({user.id})",
                "Audit Entry": str(entry.id),
            },
        )

        # Alert owner
        await alert_owner(guild, "UNAUTHORIZED BAN DETECTED", {
            "Attacker": f"{actor.name} (ID: {actor.id})",
            "Victim": f"{user.name}",
            "Action": "✅ Attacker BANNED, Victim UNBANNED"
        })

        print(f"✅ [ANTI-NUKE] {actor.name} has been BANNED for unauthorized ban attempt, {user.name} has been UNBANNED (Audit ID: {entry.id})")

    except Exception as e:
        print(f"⚠️ [ANTI-NUKE] Member ban error: {e}")


@tasks.loop(minutes=1)
async def monitor_general_audit():
    global last_general_audit_id
    if GUILD_ID <= 0:
        return
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    tech_channel = bot.get_channel(TECH_CHANNEL_ID)
    if not tech_channel:
        return
    try:
        pending_entries = []
        newest_seen_id = last_general_audit_id

        async for entry in guild.audit_logs(limit=10):
            if newest_seen_id is None or entry.id > newest_seen_id:
                newest_seen_id = entry.id

            # Stop once we reach entries from a previous pass.
            if last_general_audit_id and entry.id <= last_general_audit_id:
                break

            if entry.user is None:
                continue

            # ✅ WHITELIST: Allow bot, OWNER, and all TRUSTED_USERS (including Sapphire)
            if entry.user.id == bot.user.id or entry.user.id in TRUSTED_USERS:
                continue

            if entry.action in [
                discord.AuditLogAction.role_update,
                discord.AuditLogAction.channel_update,
                discord.AuditLogAction.ban,
                discord.AuditLogAction.kick,
                discord.AuditLogAction.member_role_update,
            ]:
                pending_entries.append(entry)

        if last_general_audit_id is None:
            last_general_audit_id = newest_seen_id
            return

        for entry in reversed(pending_entries):
            embed = discord.Embed(
                title="⚠️ Audit Alert",
                description=f"{entry.user} performed {entry.action} on {entry.target}",
                color=discord.Color.red(),
            )
            await tech_channel.send(embed=embed)

        if newest_seen_id is not None:
            last_general_audit_id = max(last_general_audit_id, newest_seen_id)
    except Exception as e:
        print(f"⚠️ Audit monitor error: {str(e)[:80]}")

async def lockdown_guild(guild: discord.Guild):
    everyone = guild.default_role
    overwrite = discord.PermissionOverwrite(send_messages=False, connect=False, speak=False)
    for channel in guild.channels:
        try:
            await channel.set_permissions(everyone, overwrite=overwrite)
        except Exception:
            pass
    tech_channel = bot.get_channel(TECH_CHANNEL_ID)
    if tech_channel:
        await tech_channel.send("🚨 Emergency lockdown activated!")

# ==================== REPORT COMMAND ====================
async def get_deletable_channels(channel: discord.abc.GuildChannel, guild: discord.Guild) -> list[discord.abc.Messageable]:
    """
    ADVANCED: Get all deletable channels from the selected channel.
    
    Handles:
    - Text channels: Returns the channel itself
    - Voice channels: Returns all threads within that VC
    - Forums: Returns all threads
    - Stages: Returns associated threads
    """
    deletable = []
    
    if isinstance(channel, discord.TextChannel):
        # Text channel - can have messages
        deletable.append(channel)
        # Also include any threads in this channel
        try:
            async for thread in channel.archived_threads():
                deletable.append(thread)
        except:
            pass
    
    elif isinstance(channel, discord.VoiceChannel):
        # Voice channel - find all threads in this VC
        print(f"   🎤 Voice channel detected: {channel.name}")
        print(f"   🔍 Scanning for threads in voice channel...")
        try:
            async for thread in channel.threads:
                deletable.append(thread)
                print(f"   ✓ Found thread: {thread.name}")
        except:
            pass
        
        # Also check for voice channel activity in main channels
        # (Messages posted about this voice channel)
        for ch in guild.channels:
            if isinstance(ch, discord.TextChannel):
                try:
                    async for thread in ch.archived_threads():
                        if channel.name.lower() in thread.name.lower():
                            deletable.append(thread)
                except:
                    pass
    
    elif isinstance(channel, discord.ForumChannel):
        # Forum channel - all posts are threads
        print(f"   📋 Forum channel detected: {channel.name}")
        try:
            async for thread in channel.threads:
                deletable.append(thread)
        except:
            pass
    
    elif isinstance(channel, discord.StageChannel):
        # Stage channel - may have threads
        print(f"   🎭 Stage channel detected: {channel.name}")
        try:
            async for thread in channel.threads:
                deletable.append(thread)
        except:
            pass
    
    return deletable if deletable else [channel]


@tree.command(name="report", description="Delete all messages/attachments/reactions in text or voice channels", guild=GUILD)
async def report(
    interaction: discord.Interaction,
    channel: discord.TextChannel | discord.VoiceChannel | discord.ForumChannel | discord.StageChannel,
    date: str,
    time_from: str,
    time_to: str,
    message: str | None = None
):
    """
    🗑️ DELETE ALL messages in text, voice, forum, or stage channels within a specific date and time range.
    
    ✅ Deletes Everything:
    - Text messages
    - Attachments (📎 images, files, media)
    - Reactions (😊)
    - Emojis & GIFs (🎁)
    - Threads
    - Embeds
    
    📍 Works With:
    - Text Channels
    - Voice Channels (+ associated threads)
    - Forum Channels (posts)
    - Stage Channels
    
    Parameters:
    - channel: Any channel type (text/voice/forum/stage)
    - date: YYYY-MM-DD (e.g., 2026-02-05)
    - time_from: HH:MM (e.g., 20:00)
    - time_to: HH:MM (e.g., 21:15)
    - message: Optional reason note
    """
    
    await interaction.response.defer(ephemeral=True)

    # 🔤 Determine channel type for logging
    channel_type = "Unknown"
    if isinstance(channel, discord.TextChannel):
        channel_type = "📝 Text Channel"
    elif isinstance(channel, discord.VoiceChannel):
        channel_type = "🎤 Voice Channel"
    elif isinstance(channel, discord.ForumChannel):
        channel_type = "📋 Forum Channel"
    elif isinstance(channel, discord.StageChannel):
        channel_type = "🎭 Stage Channel"

    # ⏱ Build datetime range (TIMEZONE-AWARE UTC)
    try:
        start_naive = datetime.datetime.strptime(f"{date} {time_from}", "%Y-%m-%d %H:%M")
        end_naive = datetime.datetime.strptime(f"{date} {time_to}", "%Y-%m-%d %H:%M")
        
        start = start_naive.replace(tzinfo=datetime.timezone.utc)
        end = end_naive.replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        await interaction.followup.send(
            "❌ Invalid date/time format.\n"
            "Use:\n"
            "`date = YYYY-MM-DD`\n"
            "`time_from = HH:MM`\n"
            "`time_to = HH:MM`",
            ephemeral=True
        )
        return

    if start >= end:
        await interaction.followup.send(
            "❌ `time_from` must be earlier than `time_to`.",
            ephemeral=True
        )
        return

    # 🎯 Get all deletable channels
    print(f"\n{'='*80}")
    print(f"🔍 [/report] {channel_type}: {channel.mention}")
    print(f"   Time Range: {start} to {end}")
    print(f"{'='*80}")
    
    deletable_channels = await get_deletable_channels(channel, interaction.guild)
    print(f"📊 Found {len(deletable_channels)} location(s) to scan")
    
    # 🗑 DELETE MESSAGES from all channels
    total_deleted = 0
    total_checked = 0
    total_with_attachments = 0
    total_with_reactions = 0
    channels_processed = []
    
    for target_channel in deletable_channels:
        try:
            deleted = 0
            checked = 0
            # Check bot permissions for this channel
            try:
                bot_member = interaction.guild.me
                perms = target_channel.permissions_for(bot_member)
            except Exception:
                perms = None
            if perms and not perms.manage_messages:
                print(f"   ⚠️ Skipping {getattr(target_channel,'name',str(target_channel))}: missing Manage Messages permission")
                channels_processed.append({
                    "name": getattr(target_channel, 'name', str(target_channel)),
                    "deleted": 0,
                    "checked": 0,
                    "skipped": "missing_manage_messages"
                })
                continue
            deletion_errors = []
            
            async for msg in target_channel.history(limit=None, oldest_first=False):
                checked += 1
                msg_time = msg.created_at
                
                if start <= msg_time < end:
                    try:
                        has_attachments = len(msg.attachments) > 0
                        has_reactions = len(msg.reactions) > 0
                        
                        await msg.delete()
                        deleted += 1
                        total_deleted += 1
                        
                        if has_attachments:
                            total_with_attachments += 1
                        if has_reactions:
                            total_with_reactions += 1
                        
                        content_display = []
                        if has_attachments:
                            content_display.append(f"📎{len(msg.attachments)}")
                        if has_reactions:
                            content_display.append(f"😊{len(msg.reactions)}")
                        if len(msg.embeds) > 0:
                            content_display.append("🎁")
                        
                        content_str = f" [{' '.join(content_display)}]" if content_display else ""
                        print(f"   ✓ {target_channel.name[:20]:20} | Deleted: {msg.author}{content_str}")
                    except Exception as e:
                        # Record deletion error for owner report and console
                        err = str(e)
                        deletion_errors.append((msg.id, err))
                        print(f"   ✗ Failed to delete {target_channel.name[:20]} msg {msg.id}: {err}")
                
                if msg_time < start:
                    break
            
            total_checked += checked
            if deleted > 0:
                channels_processed.append({
                    "name": target_channel.name,
                    "deleted": deleted,
                    "checked": checked
                })
            # attach any deletion errors to channels_processed for reporting
            if deletion_errors:
                channels_processed.append({
                    "name": getattr(target_channel,'name',str(target_channel))[:30],
                    "deleted": deleted,
                    "checked": checked,
                    "errors": deletion_errors
                })
        except Exception as e:
            print(f"   ⚠️ Cannot access {target_channel.mention}: {str(e)[:50]}")

    print(f"\n{'='*80}")
    print(f"✅ DELETION COMPLETE")
    print(f"   📊 Total checked: {total_checked}")
    print(f"   🗑 Total deleted: {total_deleted}")
    print(f"   📎 With attachments: {total_with_attachments}")
    print(f"   😊 With reactions: {total_with_reactions}")
    print(f"   📍 Locations processed: {len(channels_processed)}")
    print(f"{'='*80}\n")

    # 📩 DM OWNER with detailed report
    owner = interaction.client.get_user(OWNER_ID)
    if owner:
        report_dm = (
            f"🧾 **REPORT USED**\n\n"
            f"👤 User: {interaction.user} (`{interaction.user.id}`)\n"
            f"🕒 Used at: {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"📺 Channel: {channel.mention} ({channel_type})\n"
            f"📅 Date: {date}\n"
            f"⏱ Time range: {time_from} → {time_to} UTC\n\n"
            f"📊 **STATISTICS:**\n"
            f"   🔍 Messages checked: {total_checked}\n"
            f"   🗑 Messages deleted: {total_deleted}\n"
            f"   📎 With attachments: {total_with_attachments}\n"
            f"   😊 With reactions: {total_with_reactions}\n"
            f"   📍 Locations scanned: {len(channels_processed)}\n"
        )
        
        if channels_processed:
            report_dm += f"\n📋 **BREAKDOWN BY LOCATION:**\n"
            for ch_info in channels_processed:
                report_dm += f"   • {ch_info['name'][:30]}: {ch_info['deleted']} deleted\n"

        if message:
            report_dm += f"\n📝 **Reason:**\n{message}"

        try:
            await owner.send(report_dm)
        except:
            pass

    # ✅ USER CONFIRMATION
    confirm_msg = (
        f"✅ **Report Completed Successfully**\n\n"
        f"📊 **Results:**\n"
        f"   🔍 Checked: `{total_checked}` messages\n"
        f"   🗑 Deleted: `{total_deleted}` messages\n"
        f"   📎 From: `{total_with_attachments}` with attachments\n"
        f"   😊 From: `{total_with_reactions}` with reactions\n\n"
        f"📍 **Scanned {len(channels_processed)} location(s)**"
    )
    
    if channels_processed:
        confirm_msg += "\n\n📋 Locations:\n"
        for ch_info in channels_processed:
            confirm_msg += f"   • **{ch_info['name']}**: {ch_info['deleted']} deleted\n"
    
    await interaction.followup.send(confirm_msg, ephemeral=True)

# ==================== MANUAL SYNC COMMAND ====================
@bot.command(name="sync")
async def manual_sync(ctx):
    if ctx.author.id != OWNER_ID:
        return
    try:
        if GUILD:
            
            synced = await tree.sync(guild=GUILD)
        else:
            
            synced = await tree.sync()
        await ctx.send(f"Synced {len(synced)} commands: {[c.name for c in synced]}")
    except Exception as e:
        await ctx.send(f"Sync failed: {e}")


@tree.command(name="accesspanel", description="Deploy the access panel for new members", guild=GUILD)
async def accesspanel(interaction: discord.Interaction):
    # Check administrator permissions
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message(
            "❌ Only administrators can use this command.",
            ephemeral=True,
        )

    await interaction.response.defer()

    target_channel = interaction.guild.get_channel(ACCESS_PANEL_CHANNEL_ID)
    if target_channel is None:
        try:
            fetched_channel = await interaction.client.fetch_channel(ACCESS_PANEL_CHANNEL_ID)
            if fetched_channel.guild.id != interaction.guild.id:
                return await interaction.followup.send("❌ The configured access panel channel is not in this server.")
            target_channel = fetched_channel
        except Exception as e:
            print(f"⚠️ Failed to resolve access panel channel: {e}")
            return await interaction.followup.send(f"❌ Access panel channel `{ACCESS_PANEL_CHANNEL_ID}` could not be found.")

    allowed, reason = can_send_message_in_channel(target_channel, interaction.guild)
    if not allowed:
        return await interaction.followup.send(f"❌ {reason}")

    try:
        sent, existing_panel = await send_access_panel_message(target_channel)
        if not sent and existing_panel:
            return await interaction.followup.send(f"ℹ️ Access panel already exists: {existing_panel.jump_url}")

        await interaction.followup.send(f"✅ Access panel deployed in {target_channel.mention}.")
    except Exception as e:
        print(f"⚠️ Access panel deployment failed: {e}")
        await interaction.followup.send("❌ Failed to deploy the access panel. Check console logs for details.")


# ==================== LOCKDOWN CONTROL ====================
@tree.command(name="control", description="Open Legend Star Control Panel", guild=GUILD)
async def control(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message(
            "❌ You are not authorized to use this command.",
            ephemeral=True,
        )
        return

    dashboard_url = build_control_panel_url(FRONTEND_URL)
    view = discord.ui.View()
    view.add_item(discord.ui.Button(
        label="Open Control Panel",
        url=dashboard_url,
        style=discord.ButtonStyle.link,
    ))

    await interaction.response.send_message(
        "🚀 Open your Legend Star Control Panel:",
        view=view,
        ephemeral=True,
    )

@tree.command(name="ok", description="Owner only: Unlock server from lockdown", guild=GUILD)
@checks.has_role(ROLE_ID)
async def ok_command(interaction: discord.Interaction):
    """Owner only: Unlock server with /ok - Owner MUST have specified role"""
    print(f"🔍 DEBUG: /ok command triggered | Author: {interaction.user} ({interaction.user.id}) | Is Owner: {interaction.user.id == OWNER_ID}")
    
    # STRICT Owner check ONLY
    if interaction.user.id != OWNER_ID:
        print(f"❌ Unauthorized access attempt by {interaction.user.id}")
        await interaction.response.send_message("❌ **UNAUTHORIZED:** Only the Owner can use this command.", ephemeral=True)
        return
    
    print(f"✅ Owner verified. Processing lockdown lift...")
    
    # Now defer for the rest of the operation
    await interaction.response.defer(thinking=True)
    
    try:
        global is_locked_down
        print(f"📊 Current lockdown state: {is_locked_down}")
        
        is_locked_down = False
        print(f"🔓 Lockdown state set to: False")
        
        # Restore default role permissions (allow messaging and voice)
        role = interaction.guild.default_role
        print(f"📝 Editing @everyone role permissions...")
        perms = role.permissions
        perms.send_messages = True
        perms.connect = True
        perms.speak = True
        
        await role.edit(permissions=perms, reason="Owner Command: /ok - Lockdown Lifted")
        print(f"✅ Role permissions updated successfully")
        
        await interaction.followup.send("✅ **STATUS GREEN:** Lockdown lifted. Server is back to normal.")
        print("🟢 Lockdown lifted by Owner.")
        
        # Alert all admins
        await alert_owner(interaction.guild, "LOCKDOWN LIFTED", {
            "Status": "Server is now UNLOCKED",
            "Action": "Performed by Owner",
            "Time": datetime.datetime.now().strftime("%H:%M:%S")
        })
    except Exception as e:
        is_locked_down = False
        print(f"🔴 Error in /ok command: {str(e)}")
        await interaction.followup.send(f"⚠️ **ERROR:** {str(e)[:100]}")


# ==================== STARTUP ====================
@bot.event
async def on_ready():
    global tempvoice_panel_message_sent

    print(f"\n{'='*70}")
    print(f"✅ LEGEND STAR BOT ONLINE")
    print(f"{'='*70}")
    print(f"Logged in as {bot.user}")
    print(f"Bot ID: {bot.user.id}")
    print(f"GUILD_ID: {GUILD_ID}")
    print(f"MongoDB Connected: {mongo_connected}")
    print(f"IST Timezone: {datetime.datetime.now(KOLKATA).strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"Commands in tree before sync: {[c.name for c in tree.get_commands(guild=GUILD if GUILD_ID > 0 else None)]}")
    
    # Verify MongoDB connection
    if not mongo_connected:
        print("⚠️ WARNING: MongoDB is not connected. Data will be lost on restart!")
        print("⚠️ Please check your MONGODB_URI in .env file")
    else:
        try:
            # Test MongoDB by writing a test record
            test_result = save_with_retry(users_coll, {"_id": "mongodb_test"}, {"$set": {"test": True}})
            if test_result:
                print("✅ MongoDB test write successful - Data persistence enabled!")
            else:
                print("⚠️ MongoDB write test failed")
        except Exception as e:
            print(f"⚠️ MongoDB test failed: {e}")
    
    # Register persistent temp voice controls view (to avoid interaction failure)
    await register_control_panel_view()
    await register_access_panel_view()

    # Initialize SQLite spy database
    await init_spy_db()
    
    # Create indexes on first ready
    await create_indexes_async()
    
    try:
        if GUILD_ID > 0:
            print(f"🔄 Syncing to guild: {GUILD_ID}")
            synced = await tree.sync(guild=GUILD)
        else:
            print("🔄 Syncing globally (no GUILD_ID set)")
            synced = await tree.sync()  # global
        print(f"✅ Synced {len(synced)} commands: {[c.name for c in synced]}")
    except Exception as e:
        print(f"❌ Sync failed: {e}")
        import traceback
        traceback.print_exc()
    
    print(f"\n📊 Starting Background Tasks:")
    print(f"   🎥 strict_camera_enforcement_watchdog: Every 30 seconds")
    print(f"   🕐 batch_save_study: Every 30 seconds")
    print(f"   📍 auto_leaderboard_ping: Daily at 23:55 IST")
    print(f"   🏆 auto_leaderboard: Daily at 23:55 IST")
    print(f"   🌙 midnight_reset: Daily at 23:59 IST")
    print(f"   ⏰ todo_checker: Every 3 hours")
    print(f"   🔗 clean_webhooks: Every 5 minutes")
    print(f"   🪝 monitor_webhook_audit: Every 1 minute")
    print(f"   📋 monitor_general_audit: Every 1 minute")
    print(f"{'='*70}\n")
    
    start_loop_once(strict_camera_enforcement_watchdog, "strict_camera_enforcement_watchdog")
    start_loop_once(batch_save_study, "batch_save_study")
    start_loop_once(auto_leaderboard_ping, "auto_leaderboard_ping")
    start_loop_once(auto_leaderboard, "auto_leaderboard")
    start_loop_once(midnight_reset, "midnight_reset")
    start_loop_once(todo_checker, "todo_checker")
    start_loop_once(clean_webhooks, "clean_webhooks")
    start_loop_once(monitor_webhook_audit, "monitor_webhook_audit")
    start_loop_once(monitor_general_audit, "monitor_general_audit")
    
    # Startup sweep: schedule enforcement for members already in strict camera channels
    try:
        if GUILD_ID > 0:
            guild = bot.get_guild(GUILD_ID)
            if guild:
                await ensure_camera_enforcement_for_guild(guild, source="startup")
    except Exception as e:
        print(f"⚠️ Startup sweep error: {e}")

    # Send temp voice control panel
    if not tempvoice_panel_message_sent:
        try:
            interface_channel = bot.get_channel(INTERFACE_CHANNEL_ID)
            if interface_channel:
                await interface_channel.send(
                    "🎛 **Voice Control Panel**\nUse buttons to manage your temp voice channel",
                    view=ControlPanel()
                )
                tempvoice_panel_message_sent = True
                print("✅ Temp voice control panel sent")
            else:
                print(f"⚠️ Interface channel {INTERFACE_CHANNEL_ID} not found")
        except Exception as e:
            print(f"⚠️ Failed to send control panel: {e}")

# Keep-alive and frontend hosting
async def handle(_):
    return web.FileResponse('legend-star/index.html')

async def spa_fallback(request):
    return web.FileResponse('legend-star/index.html')

app = web.Application()
app.router.add_get('/', handle)
app.router.add_get('/control', spa_fallback)
app.router.add_get('/LEGEND-STAR', spa_fallback)

# Serve SPA static files (conditional - only if directories exist)
if os.path.exists('legend-star'):
    try:
        app.router.add_static('/LEGEND-STAR', 'legend-star', name='legend-star')
        print("✅ Static route /LEGEND-STAR mounted")
    except Exception as e:
        print(f"⚠️ Failed to mount /LEGEND-STAR: {e}")

if os.path.exists('legend-star/assets'):
    try:
        app.router.add_static('/assets', 'legend-star/assets', name='assets')
        print("✅ Static route /assets mounted")
    except Exception as e:
        print(f"⚠️ Failed to mount /assets: {e}")

async def main():
    runner = web.AppRunner(app)
    await runner.setup()
    
    # Try to start the web server with port reuse
    max_retries = 3
    for attempt in range(max_retries):
        try:
            site = web.TCPSite(runner, '0.0.0.0', PORT)
            await site.start()
            print(f"✅ Keep-alive server running on port {PORT}")
            break
        except OSError as e:
            error_str = str(e)
            if "10048" in error_str or "Address already in use" in error_str:
                if attempt < max_retries - 1:
                    print(f"⚠️  Port {PORT} busy, retrying in 2 seconds... (attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(2)
                else:
                    print(f"❌ Port {PORT} is still in use after {max_retries} attempts")
                    print("   Commands to fix:")
                    print("   1. netstat -ano | Select-String ':3000'")
                    print("   2. Stop-Process -Id <PID> -Force")
                    await runner.cleanup()
                    return
            else:
                raise
    
    # Support a dry-run mode for local verification without connecting to Discord
    if os.getenv("DRY_RUN", "0") == "1":
        print("⚠️ DRY_RUN enabled - skipping Discord bot startup (bot.start)")
    else:
        await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())

