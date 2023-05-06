from .steamwhitelist import SteamWhitelist

async def setup(bot):
    await bot.add_cog(SteamWhitelist(bot))
