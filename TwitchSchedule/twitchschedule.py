import discord
import logging
import requests
import iso8601

from redbot.core import Config
from redbot.core import commands

log = logging.getLogger("red.nevcairiel.twitchschedule")

class TwitchSchedule(commands.Cog):
    """Twitch Schedule Cog"""

    __version__ = "1.0.0"
    __author__ = ["Nevcairiel"]

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=2534873295)

        # default global settings
        default_global = { "client_id": "", "oauth": "" }
        self.config.register_global(**default_global)

        default_guild = { "channel": "" }
        self.config.register_guild(**default_guild)

    async def get_json_schedule(self, channel):
        client_id = await self.config.client_id()
        oauth = await self.config.oauth()
        r = requests.get(f"https://api.twitch.tv/helix/schedule?broadcaster_id={channel}&first=2", headers={'Client-Id': client_id, 'Authorization': f"Bearer {oauth}"})
        if r.status_code == 200:
            try:
                json = r.json()
                if isinstance(json, dict) and "data" in json:
                    data = json["data"]
                    if isinstance(data, dict) and "segments" in data and hasattr(data["segments"], "__len__"):
                        return data["segments"]
            except requests.exceptions.JSONDecodeError:
                log.exception("Parsing JSON failed, despite server reporting success")
        return None

    @commands.group(autohelp = False, invoke_without_command = True)
    @commands.guild_only()
    async def schedule(self, ctx: commands.Context) -> None:
        """Print the current twitch schedule"""
        channel = await self.config.guild(ctx.guild).channel()
        schedule = await self.get_json_schedule(channel)
        if schedule:
            async with ctx.typing():
                message = "The next scheduled Twitch streams:"
                for entry in schedule:
                    timestamp = iso8601.parse_date(entry["start_time"])
                    event_message = f"<t:{int(timestamp.timestamp())}:f> - {entry['title']}"
                    if "category" in entry and isinstance(entry["category"], dict):
                        event_message = f"{event_message} - {entry['category']['name']}"
                    if "canceled_until" in entry and entry["canceled_until"]:
                        event_message = f"~~{event_message}~~ CANCELED"
                    message += "\n" + event_message
                await ctx.send(message)

    @schedule.group()
    @commands.admin()
    @commands.guild_only()
    async def setup(self, ctx: commands.Context) -> None:
        """Twitch Schedule setup"""
        pass

    @setup.command()
    @commands.is_owner()
    async def oauth(self, ctx: commands.Context, client_id: str, token: str):
        """Set the Twitch ClientID and OAuth Token"""
        await self.config.client_id.set(client_id)
        await self.config.oauth.set(token)
        await ctx.send(f"The OAuth token has been set", delete_after=10)
        await ctx.message.delete(delay=10)
    
    @setup.command()
    @commands.admin()
    @commands.guild_only()
    async def channel(self, ctx: commands.Context, channel: str):
        """Set the Twitch channel to read the schedule for, this needs to be the numeric broadcaster id, not the channel name"""
        await self.config.guild(ctx.guild).channel.set(channel)
        await ctx.send(f"The Twitch channel has been set")
