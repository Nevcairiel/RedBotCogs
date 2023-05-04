from abc import ABC
from typing import Literal

import discord
import logging
import os
from redbot.core import Config
from redbot.core import commands

log = logging.getLogger("nevcairiel.SteamWhitelist")

class CompositeMetaClass(type(commands.Cog), type(ABC)):
    """
    This allows the metaclass used for proper type detection to
    coexist with discord.py's metaclass
    """

    pass

class SteamWhitelist(commands.Cog, metaclass=CompositeMetaClass):
    """Steam Whitelist <> Discord bridge"""
    
    __version__ = "1.0.1"
    __author__ = ["Nevcairiel"]

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1329878731)
        
        # default global settings
        default_guild = {
            "roles": [],
            "whitelist": [],
            "bans": [],
        }
        self.config.register_guild(**default_guild)
        
        # default per-user settings
        default_user = {}
        self.config.register_user(**default_user)

    def validate_steamid(self, steam_id: str) -> bool:
        return len(steam_id) == 17 and steam_id[0:5] == "76561"

    async def red_delete_data_for_user(self, *, requester: Literal["discord_deleted_user", "owner", "user", "user_strict"], user_id: int):
        """Method for finding users data inside the cog and deleting it."""
        await self.config.user_from_id(user_id).clear()

    async def user_whitelisted(self, user: discord.Member) -> bool:
        """Check if a user has a whitelisted role"""
        allowed_roles = await self.config.guild(user.guild).roles()
        return self.user_whitelisted_internal(user, allowed_roles)
    
    def user_whitelisted_internal(self, user: discord.Member, allowed_roles) -> bool:
        """Check if a user has a whitelisted role"""
        for user_role in user.roles:
            if user_role.id in allowed_roles:
                return True
                        
        return False
    
    async def update_whitelist(self, guild: discord.Guild):
        whitelist_file = self.config.guild(guild).whitelist_file
        if not whitelist_file:
            return
        
        allowed_roles = await self.config.guild(guild).roles()
        
        steamid_whitelist = []
        steamid_whitelist += await self.config.guild(guild).whitelist()

        # get bans
        bans = await self.config.guild(guild).bans()

        # collect all users
        all_users = await self.config.all_users()
        for user_id, settings in all_users.items():
            steam_id = settings["steam_id"]
            if not steam_id:
                continue

            if steam_id in bans:
                continue

            member = guild.get_member(user_id)
            if not member:
                continue

            if self.user_whitelisted_internal(member, allowed_roles):
                steamid_whitelist.append(steam_id)

        filename = await self.config.guild(guild).whitelist_file()
        filename_tmp = filename + ".tmp"
        try:
            with open(filename + ".tmp", "wb") as file:
                file.write(bytes('\n'.join(steamid_whitelist) + '\n', "utf-8"))

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

            if self.user_whitelisted_internal(member, settings["roles"]):
                await self.update_whitelist(guild)

    @commands.command(name="steamid")
    async def steamid(self, ctx: commands.Context, steam_id: str = ""):        
        """Manage your own SteamID for the Steam Whitelist"""
        steam_id = steam_id.strip()
        if steam_id:
            if self.validate_steamid(steam_id):
                await self.config.user(ctx.author).steam_id.set(steam_id)
                await ctx.send(f"{ctx.author.mention}, your Steam ID was saved", delete_after=10)

                if ctx.guild:
                    if await self.user_whitelisted(ctx.author):
                        await self.update_whitelist(ctx.guild)
                else:
                    await self.update_all_guilds_for_member(ctx.author)
            else:
                await ctx.send(f"{ctx.author.mention}, the provided SteamID is invalid. Only SteamID64 is supported (76561...)", delete_after=10)
        else:
            steam_id = await self.config.user(ctx.author).steam_id()
            await ctx.send(f"{ctx.author.mention}, your saved Steam ID is: {steam_id}", delete_after=10)
        if ctx.guild:
            await ctx.message.delete(delay=10)

    @commands.group()
    @commands.admin()
    @commands.guild_only()
    async def steamwhitelist(self, ctx: commands.Context) -> None:
        """Steam Whitelist Management"""
        pass

    @steamwhitelist.command()
    @commands.admin()
    async def info(self, ctx: commands.Context):
        """Show information about the Steam whitelist"""
        message = "Whitelisted Roles:\n"
        async with self.config.guild(ctx.guild).roles() as roles:
            for role in roles:
                message += ctx.guild.get_role(role).mention + "\n"
        
        message += "\n"
        message += "Whitelisted Steam IDs:\n"
        async with self.config.guild(ctx.guild).whitelist() as whitelist:
            for id in whitelist:
                message += id + "\n"
        await ctx.maybe_send_embed(message)

    @steamwhitelist.command()
    @commands.is_owner()
    async def addrole(self, ctx: commands.Context, role: discord.Role):
        """Add a role to be used for the Steam whitelist"""
        async with self.config.guild(ctx.guild).roles() as roles:
            if role.id not in roles:
                roles.append(role.id)
        await ctx.send(f"The role {role.mention} has been added to the whitelist. Remember to sync to apply changes.", delete_after=4)

    @steamwhitelist.command()
    @commands.is_owner()
    async def removerole(self, ctx: commands.Context, role: discord.Role):
        """Remove a role from the Steam whitelist"""
        
        async with self.config.guild(ctx.guild).roles() as roles:
            found = role.id in roles
            if found:
                roles.remove(role.id)
        
        if found:
            await ctx.send(f"The role {role.mention} was removed from the whitelist.", delete_after=4)
        else:
            await ctx.send("The role is not on the whitelist.", delete_after=4)

    @steamwhitelist.command()
    @commands.admin()
    async def add(self, ctx: commands.Context, steam_id: str):
        """Add a Steam ID to the permanent whitelist"""
        if not self.validate_steamid(steam_id):
            await ctx.send("The SteamID is not valid. Only SteamID64 is supported (76561...)", delete_after=4)    
            return
        
        async with self.config.guild(ctx.guild).whitelist() as whitelist:
            if steam_id not in whitelist:
                whitelist.append(steam_id)
        await ctx.send("The SteamID has been added to the whitelist.", delete_after=4)
        await self.update_whitelist(ctx.guild)

    @steamwhitelist.command()
    @commands.admin()
    async def remove(self, ctx: commands.Context, steam_id: str):
        """Remove a Steam ID from the permanent whitelist"""
        async with self.config.guild(ctx.guild).whitelist() as whitelist:
            found = steam_id in whitelist
            if found:
                whitelist.remove(steam_id)
        
        if found:
            await ctx.send("The SteamID was removed from the whitelist.", delete_after=4)
            await self.update_whitelist(ctx.guild)
        else:
            await ctx.send("The SteamID was not found.", delete_after=4)

    @steamwhitelist.command(name = "ban")
    @commands.admin()
    async def addban(self, ctx: commands.Context, steam_id: str):
        """Add a Steam ID to the ban list"""
        if not self.validate_steamid(steam_id):
            await ctx.send("The SteamID is not valid. Only SteamID64 is supported (76561...)", delete_after=4)
            return

        # remove from whitelist, if its on there
        async with self.config.guild(ctx.guild).whitelist() as whitelist:
            found = steam_id in whitelist
            if found:
                whitelist.remove(steam_id)

        # add to ban list
        async with self.config.guild(ctx.guild).bans() as bans:
            if steam_id not in bans:
                bans.append(steam_id)

        # respond
        await ctx.send("The SteamID has been added to the ban list.", delete_after=4)
        await self.update_whitelist(ctx.guild)

    @steamwhitelist.command(name = "unban")
    @commands.admin()
    async def removeban(self, ctx: commands.Context, steam_id: str):
        """Remove a Steam ID from the ban list"""
        async with self.config.guild(ctx.guild).bans() as bans:
            found = steam_id in bans
            if found:
                bans.remove(steam_id)

        if found:
            await ctx.send("The SteamID was removed from the ban list.", delete_after=4)
            await self.update_whitelist(ctx.guild)
        else:
            await ctx.send("The SteamID was not found.", delete_after=4)

    @steamwhitelist.command(name = "sync")
    @commands.is_owner()
    async def sync_whitelist(self, ctx: commands.Context):
        """Re-sync the whitelist to disk"""
        await self.update_whitelist(ctx.guild)
        await ctx.send("The whitelist was synced.", delete_after=4)

    @steamwhitelist.group(name = "set")
    @commands.is_owner()
    async def settings_set(self, ctx: commands.Context):
        """Cog Configuration"""
        pass

    @settings_set.command(name = "file")
    async def set_whitelist_file(self, ctx: commands.Context, filename: str):
        """Set the output file for the whitelist"""
        # test if we can open it
        try:
            with open(filename, "wb") as file:
                await self.config.guild(ctx.guild).whitelist_file.set(filename)
                await ctx.send("Whitelist file set.", delete_after=4)
        except:
            await ctx.send("Specified file is not accessible.", delete_after=4)

    ### listeners
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        guild = after.guild
        allowed_roles = await self.config.guild(after.guild).roles()
        if not allowed_roles:
            return

        was_member = self.user_whitelisted_internal(before, allowed_roles)
        is_member = self.user_whitelisted_internal(after, allowed_roles)

        if was_member != is_member:
            await self.update_whitelist(guild)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        await self.config.member(member).clear()
        if await self.user_whitelisted(member):
            await self.update_whitelist(member.guild)
