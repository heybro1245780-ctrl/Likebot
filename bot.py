import os
import json
import time
import asyncio
import aiohttp
import discord
import subprocess
from datetime import datetime, timedelta, timezone
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
DISCORD_TOKEN    = os.getenv("DISCORD_TOKEN")
API_KEY          = os.getenv("API_KEY", "like")
API_BASE_URL     = os.getenv("API_BASE_URL", "http://127.0.0.1:5000")
BOT_PREFIX       = os.getenv("BOT_PREFIX", "!")
LOG_CHANNEL_ID   = int(os.getenv("LOG_CHANNEL_ID",  "1562386401314607134"))  # log channel
ALLOWED_CHANNEL  = int(os.getenv("ALLOWED_CHANNEL", "1562382385075585034"))  # commands-only channel
OWNER_ID         = int(os.getenv("OWNER_ID",         "1390358560478728264"))  # your Discord user ID
SUBS_FILE        = "subscriptions.json"

VALID_REGIONS = {"IND", "BD", "BR", "US", "SAC", "NA", "SG", "TH", "ID", "TW", "VN", "PK"}
REGION_FLAGS  = {
    "IND": "IN", "BD": "BD", "BR": "BR", "US": "US",
    "SG": "SG", "TH": "TH", "ID": "ID", "TW": "TW",
    "VN": "VN", "PK": "PK", "SAC": "SA", "NA": "NA",
}

PLAN_DAYS = {
    "daily":    1,
    "weekly":   7,
    "monthly":  30,
    "lifetime": None,   # None = never expires
}

BOT_STATS = {
    "start_time":       time.time(),
    "total_likes_sent": 0,
    "total_commands":   0,
    "failed_requests":  0,
    "autolike_runs":    0,
}

# IST = UTC+5:30
IST = timezone(timedelta(hours=5, minutes=30))

# ── Bot setup ─────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=BOT_PREFIX, intents=intents, help_command=None)


# ── Subscription DB helpers ───────────────────────────────────────────────────
def load_subs() -> dict:
    if os.path.exists(SUBS_FILE):
        with open(SUBS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_subs(data: dict):
    with open(SUBS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def sub_expired(sub: dict) -> bool:
    exp = sub.get("expires_at")
    if exp is None:
        return False   # lifetime
    return time.time() > exp


def plan_expires_str(sub: dict) -> str:
    exp = sub.get("expires_at")
    if exp is None:
        return "♾️ Lifetime"
    dt = datetime.fromtimestamp(exp, tz=IST)
    remaining = exp - time.time()
    if remaining <= 0:
        return "⚠️ Expired"
    days = int(remaining / 86400)
    hrs  = int((remaining % 86400) / 3600)
    return f"{dt.strftime('%d %b %Y')} ({days}d {hrs}h left)"


# ── General helpers ───────────────────────────────────────────────────────────
def progress_bar(used, total, length=10):
    if total == 0:
        return "[----------] 0%"
    filled = int(length * used / total)
    bar = "#" * filled + "-" * (length - filled)
    return f"[{bar}] {int(100 * used / total)}%"


def uptime_str():
    s = int(time.time() - BOT_STATS["start_time"])
    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    return f"{h}h {m}m {s}s"


def err_embed(title, desc=""):
    return discord.Embed(title=f"❌ {title}", description=desc, color=discord.Color.red())


def ok_embed(title, desc=""):
    return discord.Embed(title=f"✅ {title}", description=desc, color=discord.Color.green())


def info_embed(title, desc=""):
    return discord.Embed(title=f"ℹ️ {title}", description=desc, color=discord.Color.blurple())


# ── Global channel restriction ────────────────────────────────────────────────
async def channel_check(ctx: commands.Context) -> bool:
    """Only allow commands in ALLOWED_CHANNEL (or DMs from owner)."""
    # Always allow in DMs (for owner)
    if ctx.guild is None:
        return True
    if ctx.channel.id == ALLOWED_CHANNEL:
        return True
    # Silently ignore — delete the message so the channel stays clean
    try:
        await ctx.message.delete()
    except Exception:
        pass
    return False

async def log(msg_or_embed):
    if LOG_CHANNEL_ID:
        ch = bot.get_channel(LOG_CHANNEL_ID)
        if ch:
            if isinstance(msg_or_embed, discord.Embed):
                await ch.send(embed=msg_or_embed)
            else:
                await ch.send(msg_or_embed)


async def api_get(path: str, params: dict = None, timeout: int = 90):
    async with aiohttp.ClientSession() as s:
        async with s.get(
            f"{API_BASE_URL}{path}",
            params=params,
            timeout=aiohttp.ClientTimeout(total=timeout)
        ) as r:
            return r.status, await r.json(content_type=None)


async def api_post(path: str, payload: dict, timeout: int = 15):
    async with aiohttp.ClientSession() as s:
        async with s.post(
            f"{API_BASE_URL}{path}",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=timeout)
        ) as r:
            return r.status, await r.json(content_type=None)


# ── Events ────────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    app_info = await bot.application_info()
    bot.owner_id = app_info.owner.id

    # Register global channel check (only respond in ALLOWED_CHANNEL)
    bot.add_check(channel_check)

    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching,
        name=f"{BOT_PREFIX}help | FF Like Bot"
    ))
    print(f"[OK] Bot: {bot.user}  Owner: {app_info.owner}  Prefix: {BOT_PREFIX}")
    print(f"[OK] Commands restricted to channel ID: {ALLOWED_CHANNEL}")

    # Start scheduled tasks
    if not daily_reset_task.is_running():
        daily_reset_task.start()
    if not daily_autolike_task.is_running():
        daily_autolike_task.start()



@bot.event
async def on_command(ctx):
    BOT_STATS["total_commands"] += 1


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(embed=discord.Embed(
            title="⏳ Cooldown",
            description=f"Try again in **{round(error.retry_after)}s**.",
            color=discord.Color.orange()
        ), delete_after=6)
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(embed=err_embed("Missing Arguments", f"Use `{BOT_PREFIX}help` for usage."))
    elif isinstance(error, commands.NotOwner):
        await ctx.send(embed=err_embed("Owner Only", "This command is restricted to the bot owner."))
    elif isinstance(error, commands.CheckFailure):
        await ctx.send(embed=err_embed("No Access", "You do not have access to use this bot.\nContact the owner."))
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        await ctx.send(embed=err_embed("Error", str(error)[:500]))


# ── Owner-only check ──────────────────────────────────────────────────────────
def owner_only():
    """Restrict command to owner only."""
    return commands.is_owner()


# ── Scheduled Tasks ───────────────────────────────────────────────────────────
@tasks.loop(time=datetime.now(IST).replace(hour=4, minute=0, second=0, microsecond=0).timetz())
async def daily_reset_task():
    """Reset daily like count at 4AM IST."""
    try:
        status, data = await api_post("/reset", {"key": API_KEY})
        embed = ok_embed("Daily Reset", "Like count reset to 0. New day started!")
        embed.set_footer(text=f"Reset at {datetime.now(IST).strftime('%d %b %Y %I:%M %p IST')}")
        await log(embed)
        print(f"[RESET] Daily count reset at {datetime.now(IST).strftime('%H:%M IST')}")
    except Exception as e:
        print(f"[RESET ERROR] {e}")


@tasks.loop(time=datetime.now(IST).replace(hour=4, minute=5, second=0, microsecond=0).timetz())
async def daily_autolike_task():
    """Auto-like all active subscribers at 4:05 AM IST."""
    subs = load_subs()
    if not subs:
        return

    BOT_STATS["autolike_runs"] += 1
    success_count = 0
    fail_count    = 0
    expired_list  = []
    results       = []

    print(f"[AUTOLIKE] Running for {len(subs)} subscribers...")

    for discord_id, sub in list(subs.items()):
        if sub_expired(sub):
            expired_list.append((discord_id, sub))
            continue

        uid    = sub["uid"]
        region = sub["region"]

        try:
            status, data = await api_get("/like", {"uid": uid, "region": region, "key": API_KEY})
            if status == 200:
                given    = data.get("LikesGivenByAPI", 0)
                nickname = data.get("PlayerNickname", uid)
                s        = data.get("status", 2)
                if s == 1:
                    BOT_STATS["total_likes_sent"] += given
                    success_count += 1
                    results.append(f"✅ `{nickname}` ({region}) +{given} likes")
                else:
                    fail_count += 1
                    results.append(f"⚠️ `{nickname}` ({region}) maxed")
            else:
                fail_count += 1
                results.append(f"❌ UID `{uid}` failed (HTTP {status})")
        except Exception as e:
            fail_count += 1
            results.append(f"❌ UID `{uid}` error: {str(e)[:60]}")

        await asyncio.sleep(2)   # slight delay between requests

    # Remove expired subs and notify users
    for discord_id, sub in expired_list:
        del subs[discord_id]
        save_subs(subs)
        try:
            user = await bot.fetch_user(int(discord_id))
            await user.send(embed=discord.Embed(
                title="⚠️ Subscription Expired",
                description=(
                    f"Your **{sub.get('plan','?')}** auto-like plan for UID `{sub['uid']}` has expired.\n"
                    f"Contact the owner to renew."
                ),
                color=discord.Color.orange()
            ))
        except Exception:
            pass

    # Log results
    result_text = "\n".join(results[:20]) if results else "No active subscribers processed."
    embed = discord.Embed(
        title="🤖 Auto-Like Run Complete",
        description=result_text,
        color=discord.Color.green() if fail_count == 0 else discord.Color.orange()
    )
    embed.add_field(name="✅ Success",  value=f"`{success_count}`",          inline=True)
    embed.add_field(name="❌ Failed",   value=f"`{fail_count}`",             inline=True)
    embed.add_field(name="🗑️ Expired", value=f"`{len(expired_list)}`",      inline=True)
    embed.set_footer(text=f"Run at {datetime.now(IST).strftime('%d %b %Y %I:%M %p IST')}")
    await log(embed)
    print(f"[AUTOLIKE] Done. Success:{success_count} Fail:{fail_count} Expired:{len(expired_list)}")


# ── !like ─────────────────────────────────────────────────────────────────────
@bot.command(name="like", aliases=["l", "send"])
@commands.is_owner()
@commands.cooldown(1, 15, commands.BucketType.user)
async def like_command(ctx, uid: str, region: str):
    """[OWNER] Send likes manually. Usage: !like <uid> <region>"""
    region = region.upper()
    if not uid.isdigit():
        return await ctx.send(embed=err_embed("Invalid UID", "UID must be numbers only."))
    if region not in VALID_REGIONS:
        return await ctx.send(embed=err_embed("Invalid Region",
            "Valid: `" + "` `".join(sorted(VALID_REGIONS)) + "`"))

    msg = await ctx.send(embed=discord.Embed(
        title="⏳ Sending Likes...",
        description=f"UID: `{uid}` | Region: `{region}`",
        color=discord.Color.blurple()
    ))

    try:
        status, data = await api_get("/like", {"uid": uid, "region": region, "key": API_KEY})
        if status == 401:
            embed = err_embed("API Key Error", "Invalid API key.")
        elif status == 200:
            s           = data.get("status", 2)
            likes_given = data.get("LikesGivenByAPI", 0)
            nickname    = data.get("PlayerNickname", "?")
            level       = data.get("Level", "?")
            p_region    = data.get("Region", region)
            after       = data.get("LikesafterCommand", 0)
            before      = data.get("LikesbeforeCommand", 0)
            remaining   = data.get("remaining", 0)
            daily_limit = data.get("daily_limit", 300)
            used        = data.get("used", 0)

            if s == 1:
                BOT_STATS["total_likes_sent"] += likes_given
                title, color = f"❤️ Likes Sent — {nickname}!", discord.Color.green()
            else:
                title, color = f"⚠️ Maxed — {nickname}", discord.Color.orange()

            embed = discord.Embed(title=title, color=color)
            embed.add_field(name="👤 Player",   value=f"**{nickname}**",         inline=True)
            embed.add_field(name="🏅 Level",    value=f"`{level}`",              inline=True)
            embed.add_field(name="🌍 Region",   value=f"`{REGION_FLAGS.get(str(p_region), str(p_region))} {p_region}`", inline=True)
            embed.add_field(name="❤️ Given",    value=f"` +{likes_given} `",     inline=True)
            embed.add_field(name="📊 Total",    value=f"`{before}` → `{after}`", inline=True)
            embed.add_field(name="📅 Quota",    value=f"{progress_bar(used, daily_limit)}  `{remaining}` left", inline=False)
        else:
            embed = err_embed("API Error", f"HTTP {status}")
    except asyncio.TimeoutError:
        embed = err_embed("Timeout", "API took too long.")
    except Exception as e:
        embed = err_embed("Error", str(e)[:400])

    await msg.edit(embed=embed)


# ── !info ─────────────────────────────────────────────────────────────────────
@bot.command(name="info", aliases=["player", "p"])
@commands.is_owner()
@commands.cooldown(1, 8, commands.BucketType.user)
async def info_command(ctx, uid: str, region: str):
    """[OWNER] Get player info. Usage: !info <uid> <region>"""
    region = region.upper()
    if not uid.isdigit():
        return await ctx.send(embed=err_embed("Invalid UID", "Numbers only."))
    if region not in VALID_REGIONS:
        return await ctx.send(embed=err_embed("Invalid Region", "Valid: `" + "` `".join(sorted(VALID_REGIONS)) + "`"))

    msg = await ctx.send(embed=info_embed("Fetching...", f"UID `{uid}` in `{region}`"))
    try:
        status, data = await api_get("/like", {"uid": uid, "region": region, "key": API_KEY})
        if status == 200:
            nickname = data.get("PlayerNickname", "?")
            level    = data.get("Level", "?")
            p_region = data.get("Region", region)
            likes    = data.get("LikesafterCommand", 0)
            embed = discord.Embed(title=f"🎮 {nickname}", color=discord.Color.blurple())
            embed.add_field(name="🆔 UID",    value=f"`{uid}`",   inline=True)
            embed.add_field(name="🏅 Level",  value=f"`{level}`", inline=True)
            embed.add_field(name="🌍 Region", value=f"`{REGION_FLAGS.get(str(p_region), str(p_region))} {p_region}`", inline=True)
            embed.add_field(name="❤️ Likes",  value=f"`{likes}`", inline=True)
        else:
            embed = err_embed("Not Found", f"UID `{uid}` in `{region}` not found.")
    except Exception as e:
        embed = err_embed("Error", str(e)[:300])
    await msg.edit(embed=embed)


# ── !remain ───────────────────────────────────────────────────────────────────
@bot.command(name="remain", aliases=["r", "quota", "q"])
@commands.is_owner()
@commands.cooldown(1, 5, commands.BucketType.user)
async def remain_command(ctx):
    """[OWNER] Check daily quota."""
    try:
        status, data = await api_get("/remain", timeout=10)
        dl   = data.get("daily_limit", 300)
        used = data.get("used", 0)
        rem  = data.get("remaining", 0)
        rst  = data.get("reset_info", "?")
        embed = discord.Embed(title="📊 Daily Quota", color=discord.Color.blurple())
        embed.add_field(name="🔢 Limit",     value=f"`{dl}`",  inline=True)
        embed.add_field(name="✅ Used",      value=f"`{used}`", inline=True)
        embed.add_field(name="🔋 Remaining", value=f"`{rem}`",  inline=True)
        embed.add_field(name="🔄 Resets At", value=f"`{rst}`",  inline=True)
        embed.add_field(name="📈 Progress",  value=progress_bar(used, dl), inline=False)
    except Exception as e:
        embed = err_embed("Error", str(e))
    await ctx.send(embed=embed)


# ── !ping ─────────────────────────────────────────────────────────────────────
@bot.command(name="ping", aliases=["status"])
@commands.is_owner()
async def ping_command(ctx):
    """[OWNER] Latency + API health."""
    ws_ms = round(bot.latency * 1000)
    t0 = time.monotonic()
    try:
        st, _ = await api_get("/remain", timeout=5)
        api_ok = st == 200
    except Exception:
        api_ok = False
    api_ms = round((time.monotonic() - t0) * 1000)

    color = discord.Color.green() if api_ok else discord.Color.red()
    embed = discord.Embed(title="🏓 Status", color=color)
    embed.add_field(name="💬 Bot",    value=f"`{ws_ms}ms`",                       inline=True)
    embed.add_field(name="🌐 API",    value=f"`{api_ms}ms`",                       inline=True)
    embed.add_field(name="🔌 Online", value="`Yes`" if api_ok else "`No`",         inline=True)
    embed.add_field(name="⏱️ Uptime", value=f"`{uptime_str()}`",                   inline=True)
    await ctx.send(embed=embed)


# ── !stats ────────────────────────────────────────────────────────────────────
@bot.command(name="stats")
@commands.is_owner()
async def stats_command(ctx):
    """[OWNER] Bot statistics."""
    subs = load_subs()
    active = sum(1 for s in subs.values() if not sub_expired(s))
    embed = discord.Embed(title="📈 Bot Statistics", color=discord.Color.gold())
    embed.add_field(name="❤️ Likes Sent",      value=f"`{BOT_STATS['total_likes_sent']}`", inline=True)
    embed.add_field(name="⌨️ Commands",         value=f"`{BOT_STATS['total_commands']}`",   inline=True)
    embed.add_field(name="❌ Failures",         value=f"`{BOT_STATS['failed_requests']}`",  inline=True)
    embed.add_field(name="🤖 AutoLike Runs",    value=f"`{BOT_STATS['autolike_runs']}`",    inline=True)
    embed.add_field(name="👥 Active Subs",      value=f"`{active}`",                        inline=True)
    embed.add_field(name="⏱️ Uptime",           value=f"`{uptime_str()}`",                  inline=True)
    await ctx.send(embed=embed)


# ── !plan add ─────────────────────────────────────────────────────────────────
@bot.command(name="plan")
@commands.is_owner()
async def plan_command(ctx, action: str, *args):
    """[OWNER] Manage subscription plans. Subcommands: add, remove, list, check, extend, run"""
    action = action.lower()

    # ── add ──────────────────────────────────────────────────────────────────
    if action == "add":
        # Usage: !plan add @user <uid> <region> <plan>
        # or:    !plan add <discord_id> <uid> <region> <plan>
        if len(args) < 4:
            return await ctx.send(embed=err_embed(
                "Usage",
                f"`{BOT_PREFIX}plan add @user <uid> <region> <plan>`\n"
                f"Plans: `daily` `weekly` `monthly` `lifetime`\n"
                f"Regions: `{'` `'.join(sorted(VALID_REGIONS))}`\n"
                f"Example: `{BOT_PREFIX}plan add @someone 123456789 IND monthly`"
            ))

        # Parse user mention or raw ID
        user_arg, uid, region, plan = args[0], args[1], args[2].upper(), args[3].lower()

        if ctx.message.mentions:
            target_user = ctx.message.mentions[0]
        else:
            try:
                target_user = await bot.fetch_user(int(user_arg))
            except Exception:
                return await ctx.send(embed=err_embed("Invalid User", "Mention a user or provide their Discord ID."))

        if not uid.isdigit():
            return await ctx.send(embed=err_embed("Invalid UID", "UID must be numbers only."))
        if region not in VALID_REGIONS:
            return await ctx.send(embed=err_embed("Invalid Region", "Valid: `" + "` `".join(sorted(VALID_REGIONS)) + "`"))
        if plan not in PLAN_DAYS:
            return await ctx.send(embed=err_embed("Invalid Plan", "Plans: `daily` `weekly` `monthly` `lifetime`"))

        subs = load_subs()
        discord_id = str(target_user.id)

        # Set expiry
        plan_days = PLAN_DAYS[plan]
        expires_at = None if plan_days is None else time.time() + plan_days * 86400

        subs[discord_id] = {
            "uid":        uid,
            "region":     region,
            "plan":       plan,
            "expires_at": expires_at,
            "added_by":   str(ctx.author.id),
            "added_at":   time.time(),
            "discord_tag": str(target_user),
        }
        save_subs(subs)

        embed = ok_embed(
            f"Plan Added — {target_user.display_name}",
            f"**User:** {target_user.mention}\n"
            f"**UID:** `{uid}`\n"
            f"**Region:** `{region}`\n"
            f"**Plan:** `{plan.capitalize()}`\n"
            f"**Expires:** {plan_expires_str(subs[discord_id])}"
        )
        await ctx.send(embed=embed)

        # DM the user
        try:
            dm_embed = discord.Embed(
                title="🎮 Auto-Like Plan Activated!",
                description=(
                    f"Your **{plan.capitalize()}** auto-like plan is now active!\n\n"
                    f"**UID:** `{uid}`\n"
                    f"**Region:** `{region}`\n"
                    f"**Likes:** Sent daily at **4:05 AM IST**\n"
                    f"**Expires:** {plan_expires_str(subs[discord_id])}"
                ),
                color=discord.Color.green()
            )
            await target_user.send(embed=dm_embed)
        except Exception:
            pass

        await log(embed)

    # ── remove ────────────────────────────────────────────────────────────────
    elif action == "remove":
        if not args:
            return await ctx.send(embed=err_embed("Usage", f"`{BOT_PREFIX}plan remove @user`"))

        if ctx.message.mentions:
            target_user = ctx.message.mentions[0]
        else:
            try:
                target_user = await bot.fetch_user(int(args[0]))
            except Exception:
                return await ctx.send(embed=err_embed("Invalid User", "Mention a user or provide Discord ID."))

        subs = load_subs()
        discord_id = str(target_user.id)

        if discord_id not in subs:
            return await ctx.send(embed=err_embed("Not Found", f"{target_user.mention} has no active plan."))

        sub = subs.pop(discord_id)
        save_subs(subs)
        await ctx.send(embed=ok_embed(
            "Plan Removed",
            f"Removed `{sub['plan']}` plan for {target_user.mention} (UID `{sub['uid']}`)"
        ))

    # ── extend ────────────────────────────────────────────────────────────────
    elif action == "extend":
        # Usage: !plan extend @user <days>
        if len(args) < 2:
            return await ctx.send(embed=err_embed("Usage", f"`{BOT_PREFIX}plan extend @user <days>`"))

        if ctx.message.mentions:
            target_user = ctx.message.mentions[0]
        else:
            try:
                target_user = await bot.fetch_user(int(args[0]))
            except Exception:
                return await ctx.send(embed=err_embed("Invalid User", "Mention a user or provide Discord ID."))

        try:
            extra_days = int(args[1])
        except ValueError:
            return await ctx.send(embed=err_embed("Invalid Days", "Provide a number of days."))

        subs = load_subs()
        discord_id = str(target_user.id)

        if discord_id not in subs:
            return await ctx.send(embed=err_embed("Not Found", f"{target_user.mention} has no plan. Use `{BOT_PREFIX}plan add` first."))

        sub = subs[discord_id]
        if sub["expires_at"] is None:
            return await ctx.send(embed=err_embed("Lifetime Plan", "This user already has a lifetime plan."))

        sub["expires_at"] = max(sub["expires_at"], time.time()) + extra_days * 86400
        save_subs(subs)
        await ctx.send(embed=ok_embed(
            "Plan Extended",
            f"Extended `{sub['plan']}` for {target_user.mention} by **{extra_days} days**\n"
            f"New expiry: {plan_expires_str(sub)}"
        ))

    # ── check ─────────────────────────────────────────────────────────────────
    elif action == "check":
        if ctx.message.mentions:
            target_user = ctx.message.mentions[0]
        elif args:
            try:
                target_user = await bot.fetch_user(int(args[0]))
            except Exception:
                return await ctx.send(embed=err_embed("Invalid User", "Mention a user or ID."))
        else:
            target_user = ctx.author

        subs = load_subs()
        discord_id = str(target_user.id)

        if discord_id not in subs:
            return await ctx.send(embed=info_embed(
                f"{target_user.display_name} — No Plan",
                "No active subscription found."
            ))

        sub = subs[discord_id]
        expired = sub_expired(sub)
        embed = discord.Embed(
            title=f"{'⚠️ Expired' if expired else '✅ Active'} — {target_user.display_name}",
            color=discord.Color.red() if expired else discord.Color.green()
        )
        embed.add_field(name="🆔 UID",      value=f"`{sub['uid']}`",              inline=True)
        embed.add_field(name="🌍 Region",   value=f"`{sub['region']}`",           inline=True)
        embed.add_field(name="📋 Plan",     value=f"`{sub['plan'].capitalize()}`", inline=True)
        embed.add_field(name="⏳ Expires",  value=plan_expires_str(sub),           inline=False)
        await ctx.send(embed=embed)

    # ── list ──────────────────────────────────────────────────────────────────
    elif action == "list":
        subs = load_subs()
        if not subs:
            return await ctx.send(embed=info_embed("No Subscribers", "No plans are active."))

        lines = []
        for did, sub in subs.items():
            tag     = sub.get("discord_tag", did)
            uid     = sub["uid"]
            region  = sub["region"]
            plan    = sub["plan"]
            expired = "⚠️" if sub_expired(sub) else "✅"
            exp_str = plan_expires_str(sub)
            lines.append(f"{expired} **{tag}** | `{uid}` `{region}` `{plan}` | {exp_str}")

        # Split into pages of 10
        pages = [lines[i:i+10] for i in range(0, len(lines), 10)]
        for i, page in enumerate(pages):
            embed = discord.Embed(
                title=f"👥 Subscribers ({len(subs)} total) — Page {i+1}/{len(pages)}",
                description="\n".join(page),
                color=discord.Color.blurple()
            )
            await ctx.send(embed=embed)

    # ── run ───────────────────────────────────────────────────────────────────
    elif action == "run":
        await ctx.send(embed=info_embed("Auto-Like Triggered", "Running auto-like for all subscribers now..."))
        await daily_autolike_task()

    else:
        await ctx.send(embed=err_embed(
            "Unknown Subcommand",
            f"Use: `add` `remove` `extend` `check` `list` `run`"
        ))


# ── !adduid ───────────────────────────────────────────────────────────────────
@bot.command(name="adduid")
@commands.is_owner()
async def adduid_command(ctx, uid: str, password: str, region: str = "BD"):
    region = region.upper()
    if not uid.isdigit():
        return await ctx.send(embed=err_embed("Invalid UID", "Numbers only."))

    file_map = {"IND": "Uid_ind.json", "BD": "Uid_bd.json"}
    filename = file_map.get(region, "Uid_bd.json")

    try:
        data = json.load(open(filename, encoding="utf-8")) if os.path.exists(filename) else []
        if any(str(e.get("uid")) == uid for e in data):
            return await ctx.send(embed=err_embed("Duplicate", f"UID `{uid}` already in `{filename}`."))
        data.append({"uid": uid, "password": password})
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        try:
            await ctx.message.delete()
        except Exception:
            pass
        await ctx.send(embed=ok_embed("UID Added", f"Added `{uid}` to `{filename}` (total: {len(data)})\nRun `{BOT_PREFIX}refresh` to update tokens."))
    except Exception as e:
        await ctx.send(embed=err_embed("Error", str(e)[:400]))


# ── !removeuid ────────────────────────────────────────────────────────────────
@bot.command(name="removeuid", aliases=["deluid", "rmuid"])
@commands.is_owner()
async def removeuid_command(ctx, uid: str, region: str = "BD"):
    region = region.upper()
    file_map = {"IND": "Uid_ind.json", "BD": "Uid_bd.json"}
    filename = file_map.get(region, "Uid_bd.json")
    try:
        if not os.path.exists(filename):
            return await ctx.send(embed=err_embed("Not Found", f"`{filename}` not found."))
        data = json.load(open(filename, encoding="utf-8"))
        new_data = [e for e in data if str(e.get("uid")) != uid]
        if len(new_data) == len(data):
            return await ctx.send(embed=err_embed("Not Found", f"UID `{uid}` not in `{filename}`."))
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(new_data, f, indent=2)
        await ctx.send(embed=ok_embed("UID Removed", f"Removed `{uid}` from `{filename}`. Remaining: {len(new_data)}"))
    except Exception as e:
        await ctx.send(embed=err_embed("Error", str(e)[:400]))


# ── !listuid ──────────────────────────────────────────────────────────────────
@bot.command(name="listuid", aliases=["uids"])
@commands.is_owner()
async def listuid_command(ctx, region: str = "BD"):
    region = region.upper()
    file_map = {"IND": "Uid_ind.json", "BD": "Uid_bd.json"}
    filename = file_map.get(region, "Uid_bd.json")
    try:
        if not os.path.exists(filename):
            return await ctx.send(embed=err_embed("Not Found", f"`{filename}` not found."))
        data = json.load(open(filename, encoding="utf-8"))
        if not data:
            return await ctx.send(embed=err_embed("Empty", f"No entries in `{filename}`."))
        lines = "\n".join([f"`{i+1}.` `{e.get('uid','?')}`" for i, e in enumerate(data[:25])])
        if len(data) > 25:
            lines += f"\n...and `{len(data)-25}` more"
        embed = discord.Embed(title=f"UIDs in {filename}", description=lines, color=discord.Color.blurple())
        embed.set_footer(text=f"Total: {len(data)}")
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(embed=err_embed("Error", str(e)[:400]))


# ── !refresh ──────────────────────────────────────────────────────────────────
@bot.command(name="refresh", aliases=["rt"])
@commands.is_owner()
async def refresh_command(ctx):
    msg = await ctx.send(embed=info_embed("Refreshing Tokens...", "Running `refresh_tokens.py`..."))
    try:
        import sys as _sys
        result = subprocess.run([_sys.executable, "refresh_tokens.py"], capture_output=True, text=True, timeout=120)
        out = (result.stdout or "(no output)")[-1500:]
        err = (result.stderr or "")[-500:]
        embed = ok_embed("Tokens Refreshed", f"```\n{out}\n```") if result.returncode == 0 else err_embed("Failed", f"```\n{out}\n{err}\n```")
    except subprocess.TimeoutExpired:
        embed = err_embed("Timeout", "Took too long (>2 min).")
    except Exception as e:
        embed = err_embed("Error", str(e)[:400])
    await msg.edit(embed=embed)


# ── !setlimit ─────────────────────────────────────────────────────────────────
@bot.command(name="setlimit")
@commands.is_owner()
async def setlimit_command(ctx, limit: int):
    try:
        status, data = await api_post("/setlimit", {"limit": limit, "key": API_KEY})
        if status == 200:
            await ctx.send(embed=ok_embed("Limit Updated", f"Daily limit set to `{limit}`"))
        else:
            await ctx.send(embed=err_embed("Error", str(data)))
    except Exception as e:
        await ctx.send(embed=err_embed("Error", str(e)[:400]))


# ── !resetcount ───────────────────────────────────────────────────────────────
@bot.command(name="resetcount", aliases=["resetdaily"])
@commands.is_owner()
async def resetcount_command(ctx):
    try:
        status, data = await api_post("/reset", {"key": API_KEY})
        if status == 200:
            await ctx.send(embed=ok_embed("Count Reset", "Daily like count has been reset to 0."))
        else:
            await ctx.send(embed=err_embed("Error", str(data)))
    except Exception as e:
        await ctx.send(embed=err_embed("Error", str(e)[:400]))


# ── !help ─────────────────────────────────────────────────────────────────────
@bot.command(name="help", aliases=["h", "commands"])
@commands.is_owner()
async def help_command(ctx):
    p = BOT_PREFIX
    embed = discord.Embed(
        title="🎮 FF Like Bot — Owner Commands",
        description="All commands are owner-only.",
        color=discord.Color.gold()
    )
    embed.add_field(name="⚡ Like Commands", value=(
        f"`{p}like <uid> <region>` — Send likes manually\n"
        f"`{p}info <uid> <region>` — Player info\n"
        f"`{p}remain` — Daily quota\n"
        f"`{p}ping` — Bot + API status\n"
        f"`{p}stats` — Statistics"
    ), inline=False)

    embed.add_field(name="📋 Subscription Plans", value=(
        f"`{p}plan add @user <uid> <region> <plan>` — Add plan\n"
        f"`{p}plan remove @user` — Remove plan\n"
        f"`{p}plan extend @user <days>` — Extend plan\n"
        f"`{p}plan check [@user]` — Check plan status\n"
        f"`{p}plan list` — List all subscribers\n"
        f"`{p}plan run` — Trigger auto-like now\n"
        f"Plans: `daily(1d)` `weekly(7d)` `monthly(30d)` `lifetime`"
    ), inline=False)

    embed.add_field(name="🔧 Token Management", value=(
        f"`{p}adduid <uid> <pass> [region]` — Add UID\n"
        f"`{p}removeuid <uid> [region]` — Remove UID\n"
        f"`{p}listuid [region]` — List UIDs\n"
        f"`{p}refresh` — Refresh all tokens"
    ), inline=False)

    embed.add_field(name="⚙️ Admin", value=(
        f"`{p}setlimit <n>` — Update daily limit\n"
        f"`{p}resetcount` — Reset daily count"
    ), inline=False)

    embed.set_footer(text=f"Auto-like runs daily at 4:05 AM IST | Prefix: {p}")
    await ctx.send(embed=embed)


# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise ValueError("DISCORD_TOKEN not set!")
    bot.run(DISCORD_TOKEN)
