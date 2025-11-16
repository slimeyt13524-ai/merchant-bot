import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Select, Button
from typing import Literal

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- CONFIG ----------
COMMAND_ROLE_ID = 1438571707186155660  # Role that can run /spawner

STAFF_ROLE_1 = 1438937907653247047  # Can claim tickets up to 1 spawner
STAFF_ROLE_2 = 1438937985511981098  # Can claim tickets up to 3 spawners
STAFF_ROLE_3 = 1438588926104441013  # Can claim tickets up to 5 spawners

SCAM_ALERT_ROLE_ID = 1438593799143161926  # Role pinged if scam
TICKET_CATEGORY_ID = 1438565668189503518  # Category where tickets are created
REQUEST_LOG_CHANNEL_ID = 1439568860540829736  # Channel for claim button


# ---------------------- UI COMPONENTS ---------------------- #

class SpawnerSelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Skeleton Spawner", value="Skeleton")
        ]
        super().__init__(placeholder="Select spawner type...", options=options)

    async def callback(self, interaction: discord.Interaction):
        self.view.spawner_type = self.values[0]
        await interaction.response.defer()


class AmountSelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=str(i), value=str(i)) for i in range(1, 6)
        ]
        super().__init__(placeholder="Select amount...", options=options)

    async def callback(self, interaction: discord.Interaction):
        self.view.spawner_amount = int(self.values[0])
        await interaction.response.defer()


class BuyButton(Button):
    def __init__(self, mode):
        super().__init__(label=f"{mode.capitalize()} Spawner(s)", style=discord.ButtonStyle.green)
        self.mode = mode

    async def callback(self, interaction: discord.Interaction):

        spawner_type = getattr(self.view, "spawner_type", None)
        spawner_amount = getattr(self.view, "spawner_amount", None)

        if not spawner_type or not spawner_amount:
            await interaction.response.send_message(
                "Please select both spawner type and amount.",
                ephemeral=True
            )
            return

        guild = interaction.guild
        category = guild.get_channel(TICKET_CATEGORY_ID)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }

        channel_name = f"{interaction.user.name}-{spawner_amount}-{self.mode}"
        ticket_channel = await guild.create_text_channel(
            channel_name, overwrites=overwrites, category=category
        )

        await ticket_channel.send(
            f"🎟️ **{self.mode.capitalize()} Ticket for {interaction.user.mention}**\n"
            f"Spawner: **Skeleton**\n"
            f"Amount: **{spawner_amount}**\n"
            f"Please wait for a merchant to be available."
        )

        log_channel = guild.get_channel(REQUEST_LOG_CHANNEL_ID)
        if log_channel:
            claim_view = ClaimView(ticket_channel, interaction.user, self.mode, spawner_amount)
            await log_channel.send(
                f"New `{self.mode}` request from {interaction.user.mention} "
                f"(**{spawner_amount} Skeleton Spawner(s)**)",
                view=claim_view
            )

        await interaction.response.send_message(
            f"Your ticket has been created: {ticket_channel.mention}",
            ephemeral=True
        )


# ---------------------- CLAIM SYSTEM ---------------------- #

class ClaimView(View):
    def __init__(self, ticket_channel, buyer_user, mode, amount):
        super().__init__(timeout=None)
        self.add_item(ClaimButton(ticket_channel, buyer_user, mode, amount))


class ClaimButton(Button):
    def __init__(self, ticket_channel, buyer_user, mode, amount):
        super().__init__(label="Claim Ticket", style=discord.ButtonStyle.blurple)
        self.ticket_channel = ticket_channel
        self.buyer_user = buyer_user
        self.mode = mode
        self.amount = amount

    async def callback(self, interaction: discord.Interaction):

        await interaction.response.defer(ephemeral=True)

        user = interaction.user
        roles = [r.id for r in user.roles]

        allowed = False
        if STAFF_ROLE_1 in roles and self.amount <= 1:
            allowed = True
        if STAFF_ROLE_2 in roles and self.amount <= 3:
            allowed = True
        if STAFF_ROLE_3 in roles and self.amount <= 5:
            allowed = True

        if not allowed:
            await interaction.followup.send("❌ You don't have permission to claim this ticket.")
            return

        try:
            await self.ticket_channel.set_permissions(
                user, view_channel=True, send_messages=True
            )
        except discord.DiscordServerError:
            await interaction.followup.send("⚠ Discord API error, try again.")
            return

        await self.ticket_channel.send(f"{user.mention} has claimed this ticket!")

        for child in self.view.children:
            child.disabled = True
        await interaction.message.edit(view=self.view)

        close_view = CloseView(self.ticket_channel, self.buyer_user, user, self.mode)
        await self.ticket_channel.send("Only staff can close this ticket.", view=close_view)

        await interaction.followup.send(f"Ticket claimed! View it: {self.ticket_channel.mention}")


# ---------------------- CLOSE SYSTEM ---------------------- #

class CloseView(View):
    def __init__(self, ticket_channel, buyer_user, staff_user, mode):
        super().__init__(timeout=None)
        self.add_item(CloseButton(ticket_channel, buyer_user, staff_user, mode))


class CloseButton(Button):
    def __init__(self, ticket_channel, buyer_user, staff_user, mode):
        super().__init__(label="Close Ticket", style=discord.ButtonStyle.red)
        self.ticket_channel = ticket_channel
        self.buyer_user = buyer_user
        self.staff_user = staff_user
        self.mode = mode

    async def callback(self, interaction: discord.Interaction):

        await interaction.response.defer(ephemeral=True)

        # ---------------- STAFF CHECK ---------------- #
        staff_roles = {STAFF_ROLE_1, STAFF_ROLE_2, STAFF_ROLE_3}

        # Collect the user's roles
        user_roles = {r.id for r in interaction.user.roles}

        # Check if the user has ANY staff role
        if not user_roles.intersection(staff_roles):
            await interaction.followup.send(
                "❌ Only staff members can close tickets.",
                ephemeral=True
            )
            return
        # ------------------------------------------------ #

        await self.ticket_channel.send(
            f"{self.buyer_user.mention}, did you get scammed? Reply **yes** or **no**."
        )

        def check(msg):
            return (
                msg.channel == self.ticket_channel
                and msg.author == self.buyer_user
                and msg.content.lower() in ["yes", "no"]
            )

        try:
            msg = await bot.wait_for("message", timeout=60, check=check)
        except:
            await self.ticket_channel.send("⏳ No response. Closing ticket.")
            await self.ticket_channel.delete()
            return

        if msg.content.lower() == "no":
            await self.ticket_channel.send("Thank you! Closing the ticket.")
            await self.ticket_channel.delete()
            return

        # ---------------- SCAM REPORTED ---------------- #
        scam_role = interaction.guild.get_role(SCAM_ALERT_ROLE_ID)

        await self.ticket_channel.send(
            f"🚨 {scam_role.mention} Scam reported by {self.buyer_user.mention}!"
        )

        # Remove staff roles from the accused staff member ONLY
        staff_role_objects = [
            interaction.guild.get_role(STAFF_ROLE_1),
            interaction.guild.get_role(STAFF_ROLE_2),
            interaction.guild.get_role(STAFF_ROLE_3)
        ]

        try:
            for r in staff_role_objects:
                if r in self.staff_user.roles:
                    await self.staff_user.remove_roles(r)
        except discord.DiscordServerError:
            await self.ticket_channel.send("⚠ Discord error during role removal.")
            return

        await self.ticket_channel.send(
            f"⚠ {self.staff_user.mention}'s staff role has been removed pending investigation.\n"
            "The ticket will remain open."
        )


# ---------------------- SLASH COMMAND ---------------------- #

@bot.tree.command(name="spawner", description="Send the spawner buy/sell menu")
@app_commands.describe(mode="Choose buy or sell")
async def spawner_command(interaction: discord.Interaction, mode: Literal["buy", "sell"]):

    command_role = interaction.guild.get_role(COMMAND_ROLE_ID)
    if command_role not in interaction.user.roles:
        await interaction.response.send_message(
            "❌ You do not have permission to use this command.",
            ephemeral=True
        )
        return

    view = View()
    view.add_item(SpawnerSelect())
    view.add_item(AmountSelect())
    view.add_item(BuyButton(mode))

    embed = discord.Embed(
        title=f"{mode.capitalize()} Skeleton Spawners",
        description=f"Select your amount and press **{mode.capitalize()} Spawner(s)**.",
        color=discord.Color.green() if mode == "buy" else discord.Color.orange()
    )

    await interaction.response.send_message(embed=embed, view=view)


# ---------------------- STARTUP ---------------------- #

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands.")
    except Exception as e:
        print(e)


# ---------------------- RUN BOT ---------------------- #

import os
bot.run(os.getenv("DISCORD_TOKEN"))
