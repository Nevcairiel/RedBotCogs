# -*- coding: utf-8 -*-
import datetime
import dateutil.parser
import hashlib
import html
import logging
import re
import time
import requests
from random import randint
from typing import Optional

import discord
from discord.ext import tasks
from redbot.core import Config, bot, checks, commands
from redbot.core.utils.chat_formatting import pagify

log = logging.getLogger("red.nevcairiel.cfmodtracker")


class CFModTracker(commands.Cog):
    """CurseForge Mod Update Tracker"""

    has_warned_about_invalid_channels = False

    def __init__(self, bot: bot.Red):
        self.bot = bot
        self.conf = Config.get_conf(self, identifier=923552983512876, force_registration=True)
        self.conf.register_guild(subscriptions=[], cache=[])
        self.conf.register_global(api_key="", interval=300)
        self.background_check_updates.start()

    @commands.group()
    async def cfmod(self, ctx: commands.Context):
        """Post when new videos are added to a YouTube channel"""

    @checks.admin_or_permissions(manage_guild=True)
    @cfmod.command()
    async def setapikey(self, ctx: commands.Context, api_key: str):
         """Set the CurseForge API key for this cog."""
         await self.conf.api_key.set(api_key)
         await ctx.message.delete()
         await ctx.send("CurseForge API key set successfully!")

    @checks.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    @cfmod.command()
    async def track(self, ctx: commands.Context, modId, channelDiscord: Optional[discord.TextChannel] = None):
        """Track a mod for updates

        The mod id needs to be specified for tracking
        If no discord channel is specified, the current channel will be setup to receive notifications

        Example:
        `[p]cfmod track 928548`
        """
        api_key = await self.conf.api_key()
        if not api_key:
            await ctx.send("CurseForge API key not set!")
            return
        if not channelDiscord:
            channelDiscord = ctx.channel
        subs = await self.conf.guild(ctx.guild).subscriptions()
        newSub = {
            "id": modId,
            "channel": {"name": channelDiscord.name, "id": channelDiscord.id},
        }
        for sub in subs:
            if sub["id"] == modId and sub["channel"]["id"] == channelDiscord.id:
                await ctx.send("This subscription already exists!")
                return
        
        data = self.get_json(newSub["id"], api_key)
        file = data["latestFiles"][0]
        if file and file["fileDate"]:
            newSub["previous_date"] = file["fileDate"]
            newSub["previous_fingerprint"] = file["fileFingerprint"]
        subs.append(newSub)
        await self.conf.guild(ctx.guild).subscriptions.set(subs)
        await ctx.send(f"Subscription added: {newSub}")

    @checks.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    @cfmod.command()
    async def remove(
        self,
        ctx: commands.Context,
        modId,
        channelDiscord: Optional[discord.TextChannel] = None,
    ):
        """Unsubscribe a Discord channel from mod updates

        If no Discord channel is specified, the subscription will be removed from all channels
        """
        subs = await self.conf.guild(ctx.guild).subscriptions()
        unsubbed = []
        for i, sub in enumerate(subs):
            if sub["id"] == modId and (not channelDiscord or sub["channel"]["id"] == channelDiscord.id):
                unsubbed.append(subs.pop(i))
        if not len(unsubbed):
            await ctx.send("Subscription not found")
            return
        await self.conf.guild(ctx.guild).subscriptions.set(subs)
        await ctx.send(f"Subscription(s) removed: {unsubbed}")

    @checks.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    @cfmod.command()
    async def customize(self, ctx: commands.Context, modId, customMessage: str = False):
        """Add a custom message to a mod notification

        [p]cfmod customize 928548 "A new Shiny! Dino version is available now!"

        You can also remove customization by not specifying any message.
        """
        subs = await self.conf.guild(ctx.guild).subscriptions()
        found = False
        for i, sub in enumerate(subs):
            if sub["id"] == modId:
                found = True
                subs[i]["custom"] = customMessage
        if not found:
            await ctx.send("Subscription not found")
            return
        await self.conf.guild(ctx.guild).subscriptions.set(subs)
        await ctx.send(f"Custom message {'added' if customMessage else 'removed'}")

    @checks.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    @cfmod.command()
    async def rolemention(
        self, ctx: commands.Context, modId, rolemention: Optional[discord.Role]
    ):
        """Adds a role mention in front of the message"""
        subs = await self.conf.guild(ctx.guild).subscriptions()
        found = False
        for i, sub in enumerate(subs):
            if sub["id"] == modId:
                found = True
                subs[i]["mention"] = rolemention.id if rolemention is not None else rolemention
        if not found:
            await ctx.send("Subscription not found")
            return
        await self.conf.guild(ctx.guild).subscriptions.set(subs)
        await ctx.send(f'Role mention {"added" if rolemention else "removed" }')

    @commands.guild_only()
    @cfmod.command(name="list")
    async def showmods(self, ctx: commands.Context):
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
                    title = f"CF Mod Subs for {channel}"
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
                title = "**CF Mod Subs**"
                if len(pages) > 1:
                    title += f" ({i}/{len(pages)})"
                await ctx.send(f"{title}\n{page}")

    @checks.is_owner()
    @cfmod.command(name="ownerlist", hidden=True)
    async def owner_list(self, ctx: commands.Context):
        """List current subscriptions for all guilds"""
        for guild in self.bot.guilds:
            await self._showsubs(ctx, guild)

    @checks.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    @cfmod.command()
    async def demo(self, ctx: commands.Context):
        """Post the latest update from all subscriptions"""
        await self._check_for_updates(ctx.message.guild, demo=True)

    async def get_json(self, modId, api_key):
        r = requests.get(f"https://api.curseforge.com/v1/mods/{modId}", headers={'X-Api-Key': api_key})
        json = r.json()
        if isinstance(json, dict):
            return json["data"]
        return None

    async def _check_for_updates(
        self,
        guild: discord.Guild,
        cache: dict = {},
        demo: bool = False,
    ):
        try:
            subs = await self.conf.guild(guild).subscriptions()
            api_key = await self.conf.api_key()
            if not api_key:
                log.warning(f"CurseForge API key not set")
                return
        except:
            return
        altered = False
        for i, sub in enumerate(subs):
            channel_id = sub["channel"]["id"]
            channel = self.bot.get_channel(int(channel_id))
            if not channel:
                if not self.has_warned_about_invalid_channels:
                    log.warning(f"Invalid channel in subscription: {channel_id}")
                continue
            if not channel.permissions_for(guild.me).send_messages:
                log.warning(f"Not allowed to post subscription to: {channel_id}")
                continue
            if not sub["id"] in cache.keys():
                try:
                    cache[sub["id"]] = await self.get_json(sub["id"], api_key)
                except Exception as e:
                    log.exception(f"Error parsing feed for {sub['id']}")
                    continue
            last_mod_time = dateutil.parser.isoparse(sub.get("previous_date", "1970-01-01T00:00:00+00:00"))
            last_mod_hash = sub.get("previous_fingerprint", "")
            data = cache[sub["id"]]
            fileDate = dateutil.parser.isoparse(data["latestFiles"][0]["fileDate"])
            if fileDate > last_mod_time and last_mod_hash is not data["latestFiles"][0]["fileFingerprint"]:
                altered = True
                subs[i]["previous_date"] = data["latestFiles"][0]["fileDate"]
                subs[i]["previous_fingerprint"] = data["latestFiles"][0]["fileFingerprint"]

                # Build custom description if one is set
                custom = sub.get("custom", False)
                if custom:
                    custom = custom.replace("%name%", data["name"])
                    custom = custom.replace("%url%", data["links"]["websiteUrl"])
                    description = f"{custom}"
                # Default descriptions
                else:
                    description = (
                        f"A new update for **{data['name']}** is available:"
                        f"\n<{data['links']['websiteUrl']}>"
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

                await channel.send(content=description, allowed_mentions=mentions)
        
        if altered:
            await self.conf.guild(guild).subscriptions.set(subs)
        self.has_warned_about_invalid_channels = True
        return cache

    @checks.is_owner()
    @cfmod.command(name="setinterval", hidden=True)
    async def set_interval(self, ctx: commands.Context, interval: int):
        """Set the interval in seconds at which to check for updates

        Very low values will probably get you rate limited

        Default is 300 seconds (5 minutes)"""
        await self.conf.interval.set(interval)
        self.background_check_updates.change_interval(seconds=interval)
        await ctx.send(f"Interval set to {await self.conf.interval()}")

    async def cog_unload(self):
        self.background_check_updates.cancel()

    @tasks.loop(seconds=1)
    async def background_check_updates(self):
        fetched = {}
        for guild in self.bot.guilds:
            api_key = await self.conf.api_key()
            if not api_key:
                continue
            update = await self._check_for_updates(guild, fetched)
            fetched.update(update)

    @background_check_updates.before_loop
    async def wait_for_red(self):
        await self.bot.wait_until_red_ready()
        interval = await self.conf.interval()
        self.background_check_updates.change_interval(seconds=interval)
