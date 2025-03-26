from .namecollector import RoleNameCollector

async def setup(bot):
    await bot.add_cog(RoleNameCollector(bot))
