from abc import ABC

import discord
import logging
import os
from redbot.core import Config
from redbot.core import commands

log = logging.getLogger("nevcairiel.RoleNameCollector")

class CompositeMetaClass(type(commands.Cog), type(ABC)):
    """
    This allows the metaclass used for proper type detection to
    coexist with discord.py's metaclass
    """

    pass

class RoleNameCollector(commands.Cog, metaclass=CompositeMetaClass):
    """Collect all names of people with a certain role and store it in a file"""
    
    __version__ = "1.0.0"
    __author__ = ["Nevcairiel"]

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=253523432)

    async def user_whitelisted(self, user: discord.Member) -> bool:
        """Check if a user has a whitelisted role"""
        guildrole = await self.config.guild(user.guild).role()
        return user.get_role(guildrole) is not None
    
    async def update_userlist(self, guild: discord.Guild):
        userlist_file = await self.config.guild(guild).userlist_file()
        if not userlist_file:
            return
        
        userrole = await self.config.guild(guild).role()    
        if not userrole:
            return
        
        guildrole = guild.get_role(userrole)
        if not guildrole:
            return

        userlist = []

        # collect all users
        for member in guildrole.members:
            if not member:
                continue

            userlist.append(member.display_name)

        filename = userlist_file
        filename_tmp = filename + ".tmp"
        try:
            with open(filename + ".tmp", "wb") as file:
                file.write(bytes('\n'.join(userlist) + '\n', "utf-8"))

            os.replace(filename_tmp, filename)
        except Exception as e:
            log.error(e)

    async def update_all_guilds_for_member(self, user: discord.User):
        """Update all guilds a user is a member of"""
        all_guilds = await self.config.all_guilds()
        for g_id, settings in all_guilds.items():
            guild = self.bot.get_guild(g_id)
            if not guild:
                continue

            member = guild.get_member(user.id)
            if not member:
                continue

            if await self.user_whitelisted(member):
                await self.update_userlist(guild)

    @commands.group()
    @commands.admin()
    @commands.guild_only()
    async def rolenamecollector(self, ctx: commands.Context) -> None:
        """Collect the names of all users with a certain role"""
        pass

    @rolenamecollector.command()
    @commands.admin()
    async def info(self, ctx: commands.Context):
        """Show information about the Role Name Collector"""
        async with self.config.guild(ctx.guild).role() as role:
            if role:
                message = "Role being tracked: "
                message += ctx.guild.get_role(role).mention

        if not message:
            message = "Not setup yet"
        await ctx.maybe_send_embed(message)

    @rolenamecollector.command()
    @commands.is_owner()
    async def setrole(self, ctx: commands.Context, role: discord.Role):
        """Set the role for the Role Name Collector"""
        await self.config.guild(ctx.guild).role.set(role.id)
        await ctx.send(f"The role {role.mention} has been set. Remember to sync to apply changes.", delete_after=4)

    @rolenamecollector.command(name = "sync")
    @commands.is_owner()
    async def sync_userlist(self, ctx: commands.Context):
        """Re-sync the name list to disk"""
        await self.update_userlist(ctx.guild)
        await ctx.send("The userlist was synced.", delete_after=4)

    @rolenamecollector.group(name = "set")
    @commands.is_owner()
    async def settings_set(self, ctx: commands.Context):
        """Cog Configuration"""
        pass

    @settings_set.command(name = "file")
    async def set_userlist_file(self, ctx: commands.Context, filename: str):
        """Set the output file for the user list"""
        # test if we can open it
        try:
            with open(filename, "wb") as file:
                await self.config.guild(ctx.guild).userlist_file.set(filename)
                await ctx.send("User file set.", delete_after=4)
        except:
            await ctx.send("Specified file is not accessible.", delete_after=4)

    ### listeners
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        guild = after.guild

        was_member = self.user_whitelisted(before)
        is_member = self.user_whitelisted(after)

        if was_member != is_member:
            await self.update_userlist(guild)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if await self.user_whitelisted(member):
            await self.update_userlist(member.guild)
