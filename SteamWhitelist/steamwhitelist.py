import asyncio
from abc import ABC
from typing import Literal, Optional, Dict

import discord
import logging
import os
from discord.ext.commands import Converter, BadArgument
from redbot.core import Config
from redbot.core import commands, app_commands
from redbot.core.utils.chat_formatting import humanize_list

log = logging.getLogger("nevcairiel.SteamWhitelist")

class CompositeMetaClass(type(commands.Cog), type(ABC)):
    """
    This allows the metaclass used for proper type detection to
    coexist with discord.py's metaclass
    """

    pass

class ButtonStyleConverter(Converter):
    async def convert(self, ctx: commands.Context, argument: str) -> discord.ButtonStyle:
        available_styles = [
            i for i in dir(discord.ButtonStyle) if not i.startswith("_") and i != "try_value"
        ]
        if argument.lower() in available_styles:
            return getattr(discord.ButtonStyle, argument.lower())
        else:
            raise BadArgument(
                _("`{argument}` is not an available Style. Choose one from {styles}").format(
                    argument=argument, styles=humanize_list(available_styles)
                )
            )

class SteamIDEntry(discord.ui.Modal, title='Steam ID for Zebra Monkeys Community'):
    def __init__(self, steamWhitelist):
        super().__init__()
        self.steamWhitelist = steamWhitelist

    steamid = discord.ui.TextInput(
        label='Steam ID (in SteamID64 format)',
        placeholder='76561....',
    )

    async def on_submit(self, interaction: discord.Interaction):
        if await self.steamWhitelist.set_steamid(self.steamid.value, interaction.user, interaction.guild):
            await interaction.response.send_message(f"Your Steam ID was saved as: {self.steamid.value}", ephemeral=True)
        else:
            await interaction.response.send_message("The provided SteamID is invalid. Only SteamID64 is supported (76561...)", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await interaction.response.send_message("Oops! Something went wrong.", ephemeral=True)
        log.exception("Error saving SteamID")

class SteamIDEntryButton(discord.ui.Button):
    def __init__(
        self,
        steamWhitelist,
        custom_id: str,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        label: Optional[str] = None,
        emoji: Optional[str] = None,
    ):
        super().__init__(style=style, label=label, emoji=emoji, custom_id=custom_id)
        self.steamWhitelist = steamWhitelist

    async def callback(self, interaction: discord.Interaction):
        modal = SteamIDEntry(self.steamWhitelist)
        await interaction.response.send_modal(modal)

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
            "buttons": [],
        }
        self.config.register_guild(**default_guild)
        
        # default per-user settings
        default_user = {}
        self.config.register_user(**default_user)

        # view tracking
        self._ready: asyncio.Event = asyncio.Event()
        self.views: Dict[int, Dict[str, discord.ui.View]] = {}

    async def cog_load(self) -> None:
        loop = asyncio.get_running_loop()
        loop.create_task(self.load_views())

    async def cog_unload(self):
        for views in self.views.values():
            for view in views.values():
                # Don't forget to remove persistent views when the cog is unloaded.
                log.verbose("Stopping view %s", view)
                view.stop()

    def cog_check(self, ctx: commands.Context) -> bool:
        return self._ready.is_set()

    async def load_views(self):
        await self.bot.wait_until_red_ready()
        try:
            await self.initialize_buttons()
        except Exception:
            log.exception("Error initializing Buttons")
        for guild_id, guild_views in self.views.items():
            for msg_ids, view in guild_views.items():
                log.trace("Adding view %r to %s", view, guild_id)
                channel_id, message_id = msg_ids.split("-")
                self.bot.add_view(view, message_id=int(message_id))
                # These should be unique messages containing views
                # and we should track them seperately
        self._ready.set()

    async def initialize_buttons(self):
        all_settings = await self.config.all_guilds()
        for guild_id, settings in all_settings.items():
            if guild_id not in self.views:
                log.trace("Adding guild ID %s to views in buttons", guild_id)
                self.views[guild_id] = {}
            for button_data in settings["buttons"]:
                emoji = button_data["emoji"]
                if emoji is not None:
                    emoji = discord.PartialEmoji.from_str(emoji)

                message_id = button_data["message_id"]
                button = SteamIDEntryButton(
                    self,
                    custom_id=button_data["custom_id"],
                    style=button_data["style"],
                    label=button_data["label"],
                    emoji=emoji,
                )
                if message_id not in self.views[guild_id]:
                    self.views[guild_id][message_id] = discord.ui.View(timeout = None)
                    self.views[guild_id][message_id].add_item(button)

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

    async def set_steamid(self, steam_id, user, guild) -> bool:
        """Helper function to set and save the steamid of a user"""
        steam_id = steam_id.strip()
        if steam_id:
            if self.validate_steamid(steam_id):
                await self.config.user(user).steam_id.set(steam_id)
                if guild:
                    if await self.user_whitelisted(user):
                        await self.update_whitelist(guild)
                else:
                    await self.update_all_guilds_for_member(user)

                return True

        return False

    @app_commands.command()
    async def steamid(self, interaction: discord.Interaction, steam_id: str = ""):
        """Set your SteamID to be added to the community server whitelist"""
        if steam_id:
            if await self.set_steamid(steam_id, interaction.user, interaction.guild):
                await interaction.response.send_message(f"Your Steam ID was saved as: {steam_id}", ephemeral=True)
            else:
                await interaction.response.send_message("The provided SteamID is invalid. Only SteamID64 is supported (76561...)", ephemeral=True)
        else:
            await interaction.response.send_modal(SteamIDEntry(self))

    @commands.command(name="steamid")
    async def steamid_text(self, ctx: commands.Context, steam_id: str = ""):
        """Manage your own SteamID for the Steam Whitelist"""
        steam_id = steam_id.strip()
        if steam_id:
            if await self.set_steamid(steam_id, ctx.author, ctx.guild):
                await ctx.send(f"{ctx.author.mention}, your Steam ID was saved", delete_after=10)
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

    ### WIP button support
    @steamwhitelist.command(name = "sendbutton")
    @commands.is_owner()
    async def sendbutton(self, ctx: commands.Context, channel: discord.TextChannel, label: str, emoji: str, style: ButtonStyleConverter, message: str):
        """
        Send a Button to set the Steam ID

        - `channel` - Channel to send to
        - `label` - Label of the button
        - `emoji` - Emoji on the button
        - `style` - Style of the button
        - `message` - Text Message to go with it

        Example:
            [p]steamwhitelist sendbutton #test "Set Steam ID" 😀 primary "Beep-Boop, set your Steam ID by clicking the button"
        """
        if ctx.guild.id not in self.views:
            self.views[ctx.guild.id] = {}

        view = discord.ui.View(timeout=None)
        view.add_item(SteamIDEntryButton(self, custom_id="zebramonkey_steamwhitelist_button", style=style, label=label, emoji=emoji))
        msg = await channel.send(content=message, view=view)
        message_key = f"{msg.channel.id}-{msg.id}"

        self.views[ctx.guild.id][message_key] = view
        async with self.config.guild(ctx.guild).buttons() as buttons:
            buttons.append({"label": label, "emoji": emoji, "style": style, "custom_id": "zebramonkey_steamwhitelist_button", "message_id": message_key})
        # TODO: also offer a cleanup in case buttons get deleted?

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
