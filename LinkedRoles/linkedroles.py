from typing import Any

import discord
import logging

from redbot.core import Config
from redbot.core import commands

log = logging.getLogger("red.nevcairiel.linkedroles")

class LinkedRoles(commands.Cog):
    """Linked Roles Cog"""

    __version__ = "1.0.0"
    __author__ = ["Nevcairiel"]

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=298345727)

        # default global settings
        default_guild = {
            "linked_roles": {},
        }
        self.config.register_guild(**default_guild)

        default_member = {
            "stored_roles": [],
        }
        self.config.register_member(**default_member)

    async def red_delete_data_for_user(self, *, requester: Any, user_id: int):
        """Method for finding users data inside the cog and deleting it."""
        for guild in self.bot.guilds:
            await self.config.member_from_ids(guild.id, user_id).clear()

    @commands.group()
    @commands.admin()
    @commands.guild_only()
    async def linkedroles(self, ctx: commands.Context) -> None:
        """Linked Roles management"""
        pass

    @linkedroles.command()
    @commands.admin()
    @commands.guild_only()
    async def setup(self, ctx: commands.Context, role: discord.Role) -> None:
        """Setup a role to be linked to other roles

        A linked role depends on the presence of reference roles.
        If the user has the reference roles, they can manage the linked role
        on their own. If all reference roles are lost, the linked role is also
        removed, but it will be restored if any of the reference roles is regained.

        Example:
        `[p]linkedroles setup @MyRole`
        """
        linked_roles = await self.config.guild(ctx.guild).linked_roles()
        role_id = str(role.id)
        if role_id in linked_roles:
            await ctx.send("The specified role is already setup as a linked role")
            return
            
        linked_roles[role_id] = []
        await self.config.guild(ctx.guild).linked_roles.set(linked_roles)
        await ctx.send(f"Role {role} has been setup as a linked role. Add reference roles with {ctx.prefix}linkedroles addrole now.")

    @linkedroles.command()
    @commands.admin()
    @commands.guild_only()
    async def delete(self, ctx: commands.Context, role: discord.Role) -> None:
        """Delete a linked role"""
        linked_roles = await self.config.guild(ctx.guild).linked_roles()
        role_id = str(role.id)
        if role_id in linked_roles:
            del linked_roles[role_id]
            await self.config.guild(ctx.guild).linked_roles.set(linked_roles)

            # remove stored roles from all members
            members = await self.config.all_members(ctx.guild)
            for member_id, data in members.items():
                data["stored_roles"].remove(role.id)
                await self.config.member_from_ids(ctx.guild.id, member_id).set(data)
            
            await ctx.send(f"Role {role} has been removed as a linked role")
            return
        
        await ctx.send(f"Role {role} is not setup as a linked role")

    @linkedroles.command()
    @commands.admin()
    @commands.guild_only()
    async def addrole(self, ctx: commands.Context, linkedrole: discord.Role, refrole: discord.Role) -> None:
        """"Add a reference role to a linekd role

        Example:
        `[p]linkedroles addrole @MyLinkedRole @MyReferenceRole`
        """
        linked_roles = await self.config.guild(ctx.guild).linked_roles()
        role_id = str(linkedrole.id)
        if role_id in linked_roles:
            if refrole.id in linked_roles[role_id]:
                await ctx.send(f"Role {refrole} is already a reference role for {linkedrole}")
                return
            linked_roles[role_id].append(refrole.id)
            await self.config.guild(ctx.guild).linked_roles.set(linked_roles)
            await ctx.send(f"Role {refrole} has been added as a reference for {linkedrole}")
            return
            
        await ctx.send(f"Role {linkedrole} is not setup as a linked role")

    @linkedroles.command()
    @commands.admin()
    @commands.guild_only()
    async def removerole(self, ctx: commands.Context, linkedrole: discord.Role, refrole: discord.Role) -> None:
        """"Remove a reference role from a linekd role

        Example:
        `[p]linkedroles removerole @MyLinkedRole @MyReferenceRole`
        """
        linked_roles = await self.config.guild(ctx.guild).linked_roles()
        role_id = str(linkedrole.id)
        if role_id in linked_roles:
            if refrole.id not in linked_roles[role_id]:
                await ctx.send(f"Role {refrole} is not a reference role for {linkedrole}")
                return
            linked_roles[role_id].remove(refrole.id)
            await self.config.guild(ctx.guild).linked_roles.set(linked_roles)
            await ctx.send(f"Role {refrole} has been removed as a reference for {linkedrole}")
            return
            
        await ctx.send(f"Role {linkedrole} is not setup as a linked role")

    @linkedroles.command()
    @commands.admin()
    @commands.guild_only()
    async def updatemembers(self, ctx: commands.Context) -> None:
        """"Process all members after changing the linked role configuration"""
        member_count = 0
        async with ctx.typing():
            for member in ctx.guild.members:
                await self._process_member(member)

            members = await self.config.all_members(ctx.guild)
            for member_id, data in members.items():
                if data["stored_roles"]:
                    member_count += 1
        
        await ctx.send(f"There is now {member_count} members with a saved role")

    async def _process_member(self, member: discord.Member) -> None:
        """Process the provided member for linked role changes, if needed"""
        # load config for this guild
        linked_roles = await self.config.guild(member.guild).linked_roles()
        if not linked_roles:
            return
        
        # iterate over all configured roles
        for role_id, ref_roles in linked_roles.items():
            if not ref_roles:
                continue

            # convert to int (ints cant be keys in json dicts)
            role_id = int(role_id)

            # check if the user has the configured role
            role = member.get_role(role_id)

            # check if the user has any of the ref roles
            has_ref_role = False
            for ref_role_id in ref_roles:
                if member.get_role(ref_role_id):
                    has_ref_role = True
                    break
            
            # if the user has the role, check if we need to save it and remove it
            if role:
                if not has_ref_role:
                    async with self.config.member(member).stored_roles() as stored_roles:
                        if role_id not in stored_roles:
                            stored_roles.append(role_id)
                    await member.remove_roles(role, reason="Saving Linked Role")
            else:
                # or check if we need to re-add the role
                if has_ref_role:
                    # lookup the role from the guild, we need it below
                    role = member.guild.get_role(role_id)
                    if not role: # sanity check
                        return

                    # if we have the reference role, check our storage if we previously saved the role
                    stored_roles = await self.config.member(member).stored_roles()
                    if role_id in stored_roles:
                        # remove the role from storage so that the bot won't reapply it constantly
                        # this allows self-service actions to change the role
                        stored_roles.remove(role_id)
                        await self.config.member(member).stored_roles.set(stored_roles)
                        # and finally, add the role to the user
                        await member.add_roles(role, reason="Restoring Linked Role")

    ### listeners
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        await self._process_member(after)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        await self.config.member(member).clear()
