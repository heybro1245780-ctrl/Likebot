import os
import discord
import aiohttp
from discord.ext import commands

# ─── Config ───────────────────────────────────────────────────────────────────
DISCORD_TOKEN = os.getenv("MTU2MjM3MTQwNTEwNDgxMjA0Mg.GoPzjC.jxGvKafOispWHoGHhjvDkrqqQkST5H7OEG0g9w")
API_KEY       = os.getenv("API_KEY", "like")
API_BASE_URL  = os.getenv("API_BASE_URL", "http://127.0.0.1:5000")
BOT_PREFIX    = os.getenv("BOT_PREFIX", "!")

VALID_REGIONS = {"IND", "BD", "BR", "US", "SAC", "NA", "SG", "TH", "ID", "TW", "VN", "PK"}

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=BOT_PREFIX, intents=intents, help_command=None)


def status_color(status):
    return discord.Color.green() if status == 1 else discord.Color.red()

def region_flag(region):
    flags = {
        "IND": "IN", "BD": "BD", "BR": "BR", "US": "US",
        "SG": "SG", "TH": "TH", "ID": "ID", "TW": "TW",
        "VN": "VN", "PK": "PK", "SAC": "SA", "NA": "NA",
    }
    return flags.get(region, "")


@bot.event
async def on_ready():
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=f"{BOT_PREFIX}like | FF Like Bot"))
    print(f"[OK] Logged in as {bot.user}")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(embed=discord.Embed(title="Missing Arguments", description=f"Usage: {BOT_PREFIX}like <uid> <region>", color=discord.Color.red()))
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        await ctx.send(embed=discord.Embed(title="Error", description=str(error), color=discord.Color.red()))


@bot.command(name="like", aliases=["l"])
@commands.cooldown(1, 10, commands.BucketType.user)
async def like_command(ctx, uid: str, region: str):
    region = region.upper()
    if not uid.isdigit():
        return await ctx.send(embed=discord.Embed(title="Invalid UID", description="UID must be numbers only.", color=discord.Color.red()))
    if region not in VALID_REGIONS:
        return await ctx.send(embed=discord.Embed(title="Invalid Region", description=f"Valid: {' '.join(sorted(VALID_REGIONS))}", color=discord.Color.red()))

    thinking = discord.Embed(title="Sending Likes...", description=f"Processing UID {uid} in {region}...", color=discord.Color.blurple())
    msg = await ctx.send(embed=thinking)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{API_BASE_URL}/like", params={"uid": uid, "region": region, "key": API_KEY}, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                data = await resp.json(content_type=None)

        if resp.status == 401:
            embed = discord.Embed(title="API Key Error", description="Invalid API key.", color=discord.Color.red())
        elif resp.status == 200:
            s = data.get("status", 2)
            embed = discord.Embed(title="Likes Sent!" if s == 1 else "Max Likes Reached", color=status_color(s))
            embed.add_field(name="Player", value=f"**{data.get('PlayerNickname','?')}**", inline=True)
            embed.add_field(name="Level", value=f"{data.get('Level','?')}", inline=True)
            embed.add_field(name="Region", value=f"{data.get('Region', region)}", inline=True)
            embed.add_field(name="UID", value=f"{data.get('UID', uid)}", inline=True)
            embed.add_field(name="Likes Given", value=f"+{data.get('LikesGivenByAPI',0)}", inline=True)
            embed.add_field(name="Likes Now", value=f"{data.get('LikesafterCommand',0)} (was {data.get('LikesbeforeCommand',0)})", inline=True)
            embed.add_field(name="Daily Quota", value=f"{data.get('used',0)}/{data.get('daily_limit',300)} — {data.get('remaining',0)} left", inline=False)
            embed.set_footer(text=f"Req by {ctx.author.display_name}")
        else:
            embed = discord.Embed(title="API Error", description=f"HTTP {resp.status}: {data.get('error','Unknown')}", color=discord.Color.red())
    except aiohttp.ClientConnectorError:
        embed = discord.Embed(title="Connection Failed", description="Flask API not reachable.", color=discord.Color.red())
    except Exception as e:
        embed = discord.Embed(title="Error", description=str(e)[:500], color=discord.Color.red())

    await msg.edit(embed=embed)


@bot.command(name="remain", aliases=["r", "quota"])
async def remain_command(ctx):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{API_BASE_URL}/remain", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json(content_type=None)
        embed = discord.Embed(title="Daily Like Quota", color=discord.Color.blurple())
        embed.add_field(name="Limit", value=f"{data.get('daily_limit','?')}", inline=True)
        embed.add_field(name="Used", value=f"{data.get('used','?')}", inline=True)
        embed.add_field(name="Remaining", value=f"{data.get('remaining','?')}", inline=True)
        embed.add_field(name="Resets At", value=f"{data.get('reset_info','?')}", inline=True)
    except Exception as e:
        embed = discord.Embed(title="Error", description=str(e), color=discord.Color.red())
    await ctx.send(embed=embed)


@bot.command(name="help", aliases=["h"])
async def help_command(ctx):
    embed = discord.Embed(title="FF Like Bot Commands", color=discord.Color.gold())
    embed.add_field(name=f"{BOT_PREFIX}like <uid> <region>", value="Send likes.\nRegions: IND BD BR US SG TH ID TW VN PK SAC NA\nEx: !like 123456789 IND", inline=False)
    embed.add_field(name=f"{BOT_PREFIX}remain", value="Check daily quota.", inline=False)
    embed.add_field(name=f"{BOT_PREFIX}help", value="Show this.", inline=False)
    await ctx.send(embed=embed)


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise ValueError("DISCORD_TOKEN not set!")
    bot.run(DISCORD_TOKEN)
