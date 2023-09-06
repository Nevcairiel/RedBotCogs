# -*- coding: utf-8 -*-
import datetime
import dateutil.parser
import hashlib
import html
import logging
import re
import time
from random import randint
from typing import Optional

import aiohttp
import discord
import googleapiclient.discovery
from discord.ext import tasks
from redbot.core import Config, bot, checks, commands
from redbot.core.utils.chat_formatting import pagify

log = logging.getLogger("red.cbd-cogs.tube")

__all__ = ["UNIQUE_ID", "Tube"]

UNIQUE_ID = 0x547562756C6172

TIME_DEFAULT = "1970-01-01T00:00:00+00:00"
# Time tuple for use with time.mktime()
TIME_TUPLE = (*(int(x) for x in re.split(r"-|T|:|\+", TIME_DEFAULT)), 0)

# Word tokenizer
TOKENIZER = re.compile(r"([^\s]+)")


class Tube(commands.Cog):
    """A YouTube subscription cog

    Thanks to mikeshardmind(Sinbad) for the RSS cog as reference"""

    has_warned_about_invalid_channels = False

    def __init__(self, bot: bot.Red):
        self.bot = bot
        self.conf = Config.get_conf(self, identifier=UNIQUE_ID, force_registration=True)
        self.conf.register_guild(subscriptions=[], cache=[], api_key="")
        self.conf.register_global(interval=300, cache_size=500)
        self.background_get_new_videos.start()

    @commands.group()
    async def tube(self, ctx: commands.Context):
        """Post when new videos are added to a YouTube channel"""

    @checks.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    @tube.command()
    async def setapikey(self, ctx: commands.Context, api_key: str):
         """Set the YouTube API key for this cog."""
         await self.conf.guild(ctx.guild).api_key.set(api_key)
         await ctx.message.delete()
         await ctx.send("YouTube API key set successfully!")

    @checks.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    @tube.command()
    async def subscribe(
        self,
        ctx: commands.Context,
        channelYouTube,
        channelDiscord: Optional[discord.TextChannel] = None,
        publish: Optional[bool] = False,
    ):
        """Subscribe a Discord channel to a YouTube channel

        If no discord channel is specified, the current channel will be subscribed

        Adding channels by name is not supported at this time. The YouTube channel ID for this can be found in channel links on videos.

        For example, to subscribe to the channel Ctrl Shift Face, you would search YouTube for the name, then on one of the videos in the results copy the channel link. It should look like this:
        https://www.youtube.com/channel/UCKpH0CKltc73e4wh0_pgL3g

        Now take the last part of the link as the channel ID:
        `[p]tube subscribe UCKpH0CKltc73e4wh0_pgL3g`

        Setting the `publish` flag will cause new videos to be published to the specified channel. Using this on non-announcement channels may result in errors.
        """
        api_key = await self.conf.guild(ctx.guild).api_key()
        if not api_key:
            await ctx.send("YouTube API key not set!")
            return
        if not channelDiscord:
            channelDiscord = ctx.channel
        playlistId = self.get_upload_playlist(channelYouTube, api_key)
        if not playlistId:
            await ctx.send("Could not determine upload playlist ID")
            return
        subs = await self.conf.guild(ctx.guild).subscriptions()
        newSub = {
            "id": channelYouTube,
            "playlistId": playlistId,
            "channel": {"name": channelDiscord.name, "id": channelDiscord.id},
            "publish": publish,
        }
        newSub["uid"] = self.sub_uid(newSub)
        for sub in subs:
            if sub["uid"] == newSub["uid"]:
                await ctx.send("This subscription already exists!")
                return
        feed = self.get_feed(newSub["id"], api_key)
        last_video = feed["items"][0]
        if last_video and last_video["snippet"]["publishedAt"]:
            newSub["previous"] =  dateutil.parser.isoparse(last_video["snippet"]["publishedAt"])
            newSub["name"] = html.unescape(last_video["snippet"]["channelTitle"])
        subs.append(newSub)
        await self.conf.guild(ctx.guild).subscriptions.set(subs)
        await ctx.send(f"Subscription added: {newSub}")

    @checks.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    @tube.command()
    async def unsubscribe(
        self,
        ctx: commands.Context,
        channelYouTube,
        channelDiscord: Optional[discord.TextChannel] = None,
    ):
        """Unsubscribe a Discord channel from a YouTube channel

        If no Discord channel is specified and the asAnnouncement flag not set to True, the subscription will be removed from all channels
        """
        subs = await self.conf.guild(ctx.guild).subscriptions()
        unsubbed = []
        if channelDiscord:
            newSub = {"id": channelYouTube, "channel": {"id": channelDiscord.id}}
            unsubTarget, unsubType = self.sub_uid(newSub), "uid"
        else:
            unsubTarget, unsubType = channelYouTube, "id"
        for i, sub in enumerate(subs):
            if sub[unsubType] == unsubTarget:
                unsubbed.append(subs.pop(i))
        if not len(unsubbed):
            await ctx.send("Subscription not found")
            return
        await self.conf.guild(ctx.guild).subscriptions.set(subs)
        await ctx.send(f"Subscription(s) removed: {unsubbed}")

    @checks.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    @tube.command()
    async def customize(self, ctx: commands.Context, channelYouTube, customMessage: str = False):
        """Add a custom message to videos from a YouTube channel

        You can use any keys available in the RSS object in your custom message
        by surrounding the key in perecent signs, e.g.:
        [p]tube customize UCKpH0CKltc73e4wh0_pgL3g "It's ya boi %author% wish a fresh vid: %title%\\nWatch, like, subscribe, give monies, etc.

        You can also remove customization by not specifying any message.
        """
        subs = await self.conf.guild(ctx.guild).subscriptions()
        found = False
        for i, sub in enumerate(subs):
            if sub["id"] == channelYouTube:
                found = True
                subs[i]["custom"] = customMessage
        if not found:
            await ctx.send("Subscription not found")
            return
        await self.conf.guild(ctx.guild).subscriptions.set(subs)
        await ctx.send(f"Custom message {'added' if customMessage else 'removed'}")

    @checks.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    @tube.command()
    async def rolemention(
        self, ctx: commands.Context, channelYouTube, rolemention: Optional[discord.Role]
    ):
        """Adds a role mention in front of the message"""
        subs = await self.conf.guild(ctx.guild).subscriptions()
        found = False
        for i, sub in enumerate(subs):
            if sub["id"] == channelYouTube:
                found = True
                subs[i]["mention"] = rolemention.id if rolemention is not None else rolemention
        if not found:
            await ctx.send("Subscription not found")
            return
        await self.conf.guild(ctx.guild).subscriptions.set(subs)
        await ctx.send(f'Role mention {"added" if rolemention else "removed" }')

    @commands.guild_only()
    @tube.command(name="list")
    async def showsubs(self, ctx: commands.Context):
        """List current subscriptions"""
        await self._showsubs(ctx, ctx.guild)

    async def _showsubs(self, ctx: commands.Context, guild: discord.Guild):
        subs = await self.conf.guild(guild).subscriptions()
        if not len(subs):
            await ctx.send("No subscriptions yet - try adding some!")
            return
        subs_by_channel = {}
        for sub in subs:
            # Channel entry must be max 124 chars: 103 + 2 + 18 + 1
            channel = f'{sub["channel"]["name"][:103]} ({sub["channel"]["id"]})'  # Max 124 chars
            subs_by_channel[channel] = [
                # Sub entry must be max 100 chars: 45 + 2 + 24 + 4 + 25 = 100
                f"{sub.get('name', sub['id'][:45])} ({sub['id']}) - {sub.get('previous', 'Never')}",
                # Preserve previous entries
                *subs_by_channel.get(channel, []),
            ]
        if ctx.channel.permissions_for(guild.me).embed_links:
            for channel, sub_ids in subs_by_channel.items():
                page_count = (len(sub_ids) // 9) + 1
                page = 1
                while len(sub_ids) > 0:
                    # Generate embed with max 1024 chars
                    embed = discord.Embed()
                    title = f"Tube Subs for {channel}"
                    embed.description = "\n".join(sub_ids[0:9])
                    if page_count > 1:
                        title += f" ({page}/{page_count})"
                        page += 1
                    embed.title = title
                    await ctx.send(embed=embed)
                    del sub_ids[0:9]
        else:
            subs_string = ""
            for channel, sub_ids in subs_by_channel.items():
                subs_string += f"\n\n{channel}"
                for sub in sub_ids:
                    subs_string += f"\n{sub}"
            pages = pagify(subs_string, delims=["\n\n"], shorten_by=12)
            for i, page in enumerate(pages):
                title = "**Tube Subs**"
                if len(pages) > 1:
                    title += f" ({i}/{len(pages)})"
                await ctx.send(f"{title}\n{page}")

    @checks.is_owner()
    @tube.command(name="ownerlist", hidden=True)
    async def owner_list(self, ctx: commands.Context):
        """List current subscriptions for all guilds"""
        for guild in self.bot.guilds:
            await self._showsubs(ctx, guild)

    def sub_uid(self, subscription: dict):
        """A subscription must have a unique combination of YouTube channel ID and Discord channel"""
        try:
            canonicalString = f'{subscription["id"]}:{subscription["channel"]["id"]}'
        except KeyError:
            raise ValueError("Subscription object is malformed")
        return hashlib.sha256(canonicalString.encode()).hexdigest()

    @checks.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    @tube.command(name="update")
    async def get_new_videos(self, ctx: commands.Context):
        """Update feeds and post new videos"""
        await ctx.send(f"Updating subscriptions for {ctx.message.guild}")
        await self._get_new_videos(ctx.message.guild, ctx=ctx)

    @checks.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    @tube.command()
    async def demo(self, ctx: commands.Context):
        """Post the latest video from all subscriptions"""
        await self._get_new_videos(ctx.message.guild, ctx=ctx, demo=True)

    @checks.is_owner()
    @tube.command(name="ownerupdate", hidden=True)
    async def owner_get_new_videos(self, ctx: commands.Context):
        """Update feeds and post new videos for all guilds"""
        fetched = {}
        for guild in self.bot.guilds:
            await ctx.send(f"Updating subscriptions for {guild}")
            update = await self._get_new_videos(guild, fetched, ctx)
            if not update:
                continue
            fetched.update(update)

    async def _get_new_videos(
        self,
        guild: discord.Guild,
        cache: dict = {},
        ctx: commands.Context = None,
        demo: bool = False,
    ):
        try:
            subs = await self.conf.guild(guild).subscriptions()
            history = await self.conf.guild(guild).cache()
            api_key = await self.conf.guild(guild).api_key()
            if not api_key:
                log.warning(f"YouTube API key not set")
                return
        except:
            return
        new_history = []
        altered = False
        for i, sub in enumerate(subs):
            publish = sub.get("publish", False)
            channel_id = sub["channel"]["id"]
            channel = self.bot.get_channel(int(channel_id))
            if not channel:
                if not self.has_warned_about_invalid_channels:
                    log.warning(f"Invalid channel in subscription: {channel_id}")
                continue
            if not channel.permissions_for(guild.me).send_messages:
                log.warning(f"Not allowed to post subscription to: {channel_id}")
                continue
            if not ("playlistId" in sub and sub["playlistId"]):
                log.warning(f"No playlist id for channel {sub['id']}")
                continue
            if not sub["id"] in cache.keys():
                try:
                    cache[sub["id"]] = self.get_feed(sub["playlistId"], api_key)
                except Exception as e:
                    log.exception(f"Error parsing feed for {sub.get('name', '')} ({sub['id']})")
                    continue
            last_video_time = dateutil.parser.isoparse(sub.get("previous", "1970-01-01T00:00:00+00:00"))
            for entry in cache[sub["id"]]["items"]:
                published = dateutil.parser.isoparse(entry["snippet"]["publishedAt"])
                if not sub.get("name"):
                    altered = True
                    sub["name"] = html.unescape(entry["snippet"]["channelTitle"])
                video_id = entry["snippet"]["resourceId"]["videoId"]
                if (published > last_video_time and not video_id in history) or (
                    demo and published > last_video_time - datetime.timedelta(seconds=1)
                ):
                    video_link = f"https://www.youtube.com/watch?v={video_id}"
                    altered = True
                    subs[i]["previous"] = entry["snippet"]["publishedAt"]
                    new_history.append(video_id)
                    # Build custom description if one is set
                    custom = sub.get("custom", False)
                    if custom:
                        for token in TOKENIZER.split(custom):
                            if token.startswith("%") and token.endswith("%"):
                                custom = custom.replace(token, html.unescape(entry["snippet"].get(token[1:-1])))
                        description = f"{custom}\n{video_link}"
                    # Default descriptions
                    else:
                        if channel.permissions_for(guild.me).embed_links:
                            # Let the embed provide necessary info
                            description = video_link
                        else:
                            description = (
                                f"New video from *{html.unescape(entry['snippet']['channelTitle'][:500])}*:"
                                f"\n**{html.unescape(entry['snippet']['title'][:500])}**\n{video_link}"
                            )

                    mention_id = sub.get("mention", False)
                    if mention_id:
                        if mention_id == guild.id:
                            description = f"{guild.default_role} {description}"
                            mentions = discord.AllowedMentions(everyone=True)
                        else:
                            description = f"<@&{mention_id}> {description}"
                            mentions = discord.AllowedMentions(roles=True)
                    else:
                        mentions = discord.AllowedMentions()

                    message = await channel.send(content=description, allowed_mentions=mentions)
                    if publish:
                        await message.publish()
        if altered:
            await self.conf.guild(guild).subscriptions.set(subs)
            await self.conf.guild(guild).cache.set(list(set([*history, *new_history])))
        self.has_warned_about_invalid_channels = True
        return cache

    @checks.is_owner()
    @tube.command(name="setinterval", hidden=True)
    async def set_interval(self, ctx: commands.Context, interval: int):
        """Set the interval in seconds at which to check for updates

        Very low values will probably get you rate limited

        Default is 300 seconds (5 minutes)"""
        await self.conf.interval.set(interval)
        self.background_get_new_videos.change_interval(seconds=interval)
        await ctx.send(f"Interval set to {await self.conf.interval()}")

    @checks.is_owner()
    @tube.command(name="setcache", hidden=True)
    async def set_cache(self, ctx: commands.Context, size: int):
        """Set the number of video IDs to cache

        Very low values may result in reposting of videos

        Default is 500"""
        await self.conf.cache_size.set(size)
        await ctx.send(f"Cache size set to {await self.conf.cache_size()}")

    def get_feed(self, playlist, api_key):
        youtube = googleapiclient.discovery.build('youtube', 'v3', developerKey=api_key, cache_discovery=False)
        return youtube.playlistItems().list(part='id,snippet', playlistId=playlist, maxResults=1).execute()

    def get_upload_playlist(self, channel, api_key):
        youtube = googleapiclient.discovery.build('youtube', 'v3', developerKey=api_key, cache_discovery=False)
        try:
            channelInfo = youtube.channels().list(part="id,contentDetails", id=channel).execute()
            playlistId = channelInfo["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
            return playlistId
        except Exception:
            log.exception("Unable to get playlist id for channel")
            return None

    async def migrate_feeds(self):
         for guild in self.bot.guilds:
            api_key = await self.conf.guild(guild).api_key()
            if not api_key:
                continue

            subs = await self.conf.guild(guild).subscriptions()
            for i, sub in enumerate(subs):
                if not ("playlistId" in sub and sub["playlistId"]):
                    playlistId = self.get_upload_playlist(sub["id"], api_key)
                    if playlistId:
                        subs[i]["playlistId"] = playlistId

            await self.conf.guild(guild).subscriptions.set(subs)

    async def cog_unload(self):
        self.background_get_new_videos.cancel()

    @tasks.loop(seconds=1)
    async def background_get_new_videos(self):
        fetched = {}
        cache_size = await self.conf.cache_size()
        for guild in self.bot.guilds:
            api_key = await self.conf.guild(guild).api_key()
            if not api_key:
                continue
            update = await self._get_new_videos(guild, fetched)
            if not update:
                continue
            fetched.update(update)
            # Truncate video ID cache
            cache = await self.conf.guild(guild).cache()
            await self.conf.guild(guild).cache.set(cache[-cache_size:])

    @background_get_new_videos.before_loop
    async def wait_for_red(self):
        await self.bot.wait_until_red_ready()
        await self.migrate_feeds()
        interval = await self.conf.interval()
        self.background_get_new_videos.change_interval(seconds=interval)
