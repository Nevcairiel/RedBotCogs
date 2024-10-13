from typing import Any

import discord
import logging

from redbot.core import Config
from redbot.core import commands

log = logging.getLogger("red.nevcairiel.adforum")

class AdForum(commands.Cog):
    """AdForum Cog"""

    __version__ = "1.0.0"
    __author__ = ["Nevcairiel"]

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=3953593295)

        default_guild = {
            "forums": {},
        }
        self.config.register_guild(**default_guild)

    async def initialize(self):
        await self.bot.wait_until_red_ready()

    async def _process_thread(self, thread: discord.Thread) -> None:
        forums = await self.config.guild(thread.guild).forums()
        forum_id = str(thread.parent_id)
        if forum_id in forums:
            await thread.owner.add_roles(thread.guild.get_role(forums[forum_id]), reason = "AdForum Thread created")

    async def _sync_forum(self, forum: discord.ForumChannel) -> None:
        forums = await self.config.guild(forum.guild).forums()
        forum_id = str(forum.id)
        if forum_id not in forums:
            log.warning("forum is not being tracked")
            return
        
        role = forum.guild.get_role(forums[forum_id])
        if not role:
            log.warning("role not found")
            return
        
        role_members = role.members
        user_list = []
        for thread in forum.threads:
            user_list.append(thread.owner)
            if thread.owner not in role_members:
                await thread.owner.add_roles(role, reason = "AdForum Thread sync")

        async for thread in forum.archived_threads(limit = None):
            user_list.append(thread.owner)
            if thread.owner not in role_members:
                await thread.owner.add_roles(role, reason = "AdForum Thread sync")

        for member in role_members:
            if member not in user_list:
                await member.remove_roles(role, reason = "AdForum Thread sync")

    @commands.group()
    @commands.admin()
    @commands.guild_only()
    async def adforum(self, ctx: commands.Context) -> None:
        """AdForum management"""
        pass

    @adforum.command()
    @commands.admin()
    @commands.guild_only()
    async def setup(self, ctx: commands.Context, forum: discord.ForumChannel, role: discord.Role) -> None:
        """Setup a new Ad Forum"""
        forums = await self.config.guild(ctx.guild).forums()
        forum_id = str(forum.id)
        if forum_id in forums:
            await ctx.send("The forum is already setup.")
            return

        forums[forum_id] = role.id
        await self.config.guild(ctx.guild).forums.set(forums)

        async with ctx.typing():
            await self._sync_forum(forum)
        
        await ctx.send("The forum was setup and synced.")
        

    @adforum.command()
    @commands.admin()
    @commands.guild_only()
    async def delete(self, ctx: commands.Context, forum: discord.ForumChannel) -> None:
        """Delete an Ad Forum"""
        forums = await self.config.guild(ctx.guild).forums()
        forum_id = str(forum.id)
        if forum_id not in forums:
            await ctx.send("The forum is not setup.")
            return
        
        del forums[forum_id]
        await self.config.guild(ctx.guild).forums.set(forums)
        await ctx.send("The forum config was deleted.")

    @adforum.command()
    @commands.admin()
    @commands.guild_only()
    async def sync(self, ctx: commands.Context, forum: discord.ForumChannel) -> None:
        """Re-sync an Ad Forum channel"""
        forums = await self.config.guild(ctx.guild).forums()
        forum_id = str(forum.id)
        if forum_id not in forums:
            await ctx.send("The forum is not setup.")
            return

        async with ctx.typing():
            await self._sync_forum(forum)

        await ctx.send("The forum was synced.")

    ### listeners
    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread) -> None:
        await self._process_thread(thread)

    @commands.Cog.listener()
    async def on_raw_thread_delete(self, payload: discord.RawThreadDeleteEvent) -> None:
        forums = await self.config.guild_from_id(payload.guild_id).forums()
        forum_id = str(payload.parent_id)
        if forum_id in forums:
            if payload.thread and payload.thread.owner:
                await payload.thread.owner.remove_roles(payload.thread.guild.get_role(forums[forum_id]), reason = "AdForum Thread deleted")
            else:
                guild = self.bot.get_guild(payload.guild_id)
                await self._sync_forum(guild.get_channel(payload.parent_id))
