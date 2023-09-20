from .linkedroles import LinkedRoles


async def setup(bot):
    await bot.add_cog(LinkedRoles(bot))
