import asyncio
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import discord
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = (os.getenv("DISCORD_TOKEN") or "").strip().strip('"').strip("'")
TEST_GUILD_ID = os.getenv("TEST_GUILD_ID")
DB_PATH = os.getenv("VOICE_DB_PATH", "voice_time.db")

if not TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN in .env file")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


VN_TZ = timezone(timedelta(hours=7), name="Asia/Ho_Chi_Minh")


def format_duration(total_seconds: int) -> str:
    hours, rem = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def parse_datetime_input(value: str) -> datetime:
    text = value.strip()

    iso_candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso_candidate)
    except ValueError:
        parsed = None

    if parsed is None:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue

    if parsed is None:
        raise ValueError("Invalid datetime format")

    if parsed.tzinfo is None:
        # User-entered naive datetime is interpreted as Vietnam time (UTC+7).
        parsed = parsed.replace(tzinfo=VN_TZ)
    else:
        parsed = parsed.astimezone(VN_TZ)

    return parsed.astimezone(timezone.utc)


def to_vn_display(dt: datetime) -> str:
    return dt.astimezone(VN_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")


@dataclass
class ActiveSession:
    channel_id: int
    joined_at: datetime


class TimeTrackerDB:
    def __init__(self, path: str = "voice_time.db") -> None:
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS channel_time (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                total_seconds INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id, channel_id)
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS voice_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def add_seconds(self, guild_id: int, user_id: int, channel_id: int, seconds: int) -> None:
        if seconds <= 0:
            return

        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO channel_time (guild_id, user_id, channel_id, total_seconds)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id, channel_id)
            DO UPDATE SET total_seconds = total_seconds + excluded.total_seconds
            """,
            (guild_id, user_id, channel_id, seconds),
        )
        self.conn.commit()

    def get_user_channel_times(self, guild_id: int, user_id: int) -> List[Tuple[int, int]]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT channel_id, total_seconds
            FROM channel_time
            WHERE guild_id = ? AND user_id = ?
            ORDER BY total_seconds DESC
            """,
            (guild_id, user_id),
        )
        return [(int(row[0]), int(row[1])) for row in cur.fetchall()]

    def get_user_channel_time(self, guild_id: int, user_id: int, channel_id: int) -> int:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT total_seconds
            FROM channel_time
            WHERE guild_id = ? AND user_id = ? AND channel_id = ?
            """,
            (guild_id, user_id, channel_id),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0

    def get_leaderboard(self, guild_id: int, limit: int = 10) -> List[Tuple[int, int]]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT user_id, SUM(total_seconds) AS grand_total
            FROM channel_time
            WHERE guild_id = ?
            GROUP BY user_id
            ORDER BY grand_total DESC
            LIMIT ?
            """,
            (guild_id, limit),
        )
        return [(int(row[0]), int(row[1])) for row in cur.fetchall()]

    def add_session(
        self,
        guild_id: int,
        user_id: int,
        channel_id: int,
        started_at: datetime,
        ended_at: datetime,
    ) -> None:
        if ended_at <= started_at:
            return

        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO voice_sessions (guild_id, user_id, channel_id, started_at, ended_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (guild_id, user_id, channel_id, started_at.isoformat(), ended_at.isoformat()),
        )
        self.conn.commit()

    def get_user_sessions_between(
        self,
        guild_id: int,
        user_id: int,
        start_at: datetime,
        end_at: datetime,
        channel_id: Optional[int] = None,
    ) -> List[Tuple[int, datetime, datetime]]:
        base_query = (
            """
            SELECT channel_id, started_at, ended_at
            FROM voice_sessions
            WHERE guild_id = ?
              AND user_id = ?
              AND ended_at > ?
              AND started_at < ?
            """
        )
        params: List[object] = [guild_id, user_id, start_at.isoformat(), end_at.isoformat()]

        if channel_id is not None:
            base_query += " AND channel_id = ?"
            params.append(channel_id)

        base_query += " ORDER BY started_at ASC"

        cur = self.conn.cursor()
        cur.execute(base_query, tuple(params))

        rows: List[Tuple[int, datetime, datetime]] = []
        for raw_channel_id, raw_start, raw_end in cur.fetchall():
            started = datetime.fromisoformat(str(raw_start)).astimezone(timezone.utc)
            ended = datetime.fromisoformat(str(raw_end)).astimezone(timezone.utc)
            rows.append((int(raw_channel_id), started, ended))

        return rows


intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True
intents.members = False

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
db = TimeTrackerDB(DB_PATH)
active_sessions: Dict[Tuple[int, int], ActiveSession] = {}
state_lock = asyncio.Lock()
commands_synced = False


async def start_tracking(member: discord.Member, channel: discord.VoiceChannel, joined_at: Optional[datetime] = None) -> None:
    key = (member.guild.id, member.id)
    async with state_lock:
        existing = active_sessions.get(key)
        if existing and existing.channel_id == channel.id:
            return

        active_sessions[key] = ActiveSession(channel_id=channel.id, joined_at=joined_at or utcnow())


async def stop_tracking(member: discord.Member, channel_id: int, left_at: Optional[datetime] = None) -> None:
    key = (member.guild.id, member.id)
    async with state_lock:
        existing = active_sessions.get(key)
        if not existing:
            return
        if existing.channel_id != channel_id:
            return

        finished_at = left_at or utcnow()
        seconds = int((finished_at - existing.joined_at).total_seconds())
        db.add_session(member.guild.id, member.id, existing.channel_id, existing.joined_at, finished_at)
        db.add_seconds(member.guild.id, member.id, existing.channel_id, seconds)
        del active_sessions[key]


async def bootstrap_current_voice_sessions() -> None:
    now = utcnow()
    for guild in client.guilds:
        for voice_channel in guild.voice_channels:
            for member in voice_channel.members:
                if member.bot:
                    continue
                await start_tracking(member, voice_channel, joined_at=now)


def merge_live_time(guild_id: int, user_id: int, base_rows: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    totals = {channel_id: seconds for channel_id, seconds in base_rows}
    key = (guild_id, user_id)

    session = active_sessions.get(key)
    if session:
        live_seconds = int((utcnow() - session.joined_at).total_seconds())
        totals[session.channel_id] = totals.get(session.channel_id, 0) + max(0, live_seconds)

    return sorted(totals.items(), key=lambda item: item[1], reverse=True)


@client.event
async def on_ready() -> None:
    global commands_synced

    if not commands_synced:
        if TEST_GUILD_ID:
            guild_obj = discord.Object(id=int(TEST_GUILD_ID))
            await tree.sync(guild=guild_obj)
        else:
            await tree.sync()
        commands_synced = True

    await bootstrap_current_voice_sessions()

    print(f"Logged in as {client.user} ({client.user.id})")


@client.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
    if member.bot:
        return

    if before.channel is None and after.channel is not None:
        await start_tracking(member, after.channel)
        return

    if before.channel is not None and after.channel is None:
        await stop_tracking(member, before.channel.id)
        return

    if before.channel is not None and after.channel is not None and before.channel.id != after.channel.id:
        await stop_tracking(member, before.channel.id)
        await start_tracking(member, after.channel)


@tree.command(name="mytime", description="Show tracked voice time (per channel) for you or another member.")
@app_commands.describe(member="Optional member to inspect")
async def mytime(interaction: discord.Interaction, member: Optional[discord.Member] = None) -> None:
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    target = member or interaction.user
    rows = db.get_user_channel_times(interaction.guild.id, target.id)
    rows = merge_live_time(interaction.guild.id, target.id, rows)

    if not rows:
        await interaction.response.send_message(f"No tracked voice time for {target.mention} yet.")
        return

    total = sum(seconds for _, seconds in rows)
    lines: List[str] = []
    for channel_id, seconds in rows:
        channel = interaction.guild.get_channel(channel_id)
        channel_name = channel.mention if channel else f"Deleted Channel ({channel_id})"
        lines.append(f"{channel_name}: **{format_duration(seconds)}**")

    message = "\n".join(lines[:20])
    if len(lines) > 20:
        message += f"\n... and {len(lines) - 20} more channels"

    await interaction.response.send_message(
        f"Voice time for {target.mention}\n"
        f"Total: **{format_duration(total)}**\n\n"
        f"{message}"
    )


@tree.command(name="channeltime", description="Show a member's tracked time in a specific voice channel.")
@app_commands.describe(channel="Voice channel", member="Optional member to inspect")
async def channeltime(
    interaction: discord.Interaction,
    channel: discord.VoiceChannel,
    member: Optional[discord.Member] = None,
) -> None:
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    target = member or interaction.user
    total = db.get_user_channel_time(interaction.guild.id, target.id, channel.id)

    key = (interaction.guild.id, target.id)
    session = active_sessions.get(key)
    if session and session.channel_id == channel.id:
        total += max(0, int((utcnow() - session.joined_at).total_seconds()))

    await interaction.response.send_message(
        f"{target.mention} in {channel.mention}: **{format_duration(total)}**"
    )


@tree.command(name="voicetop", description="Show top members by total tracked voice time.")
@app_commands.describe(limit="How many members to show (1-25)")
async def voicetop(interaction: discord.Interaction, limit: app_commands.Range[int, 1, 25] = 10) -> None:
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    leaderboard = db.get_leaderboard(interaction.guild.id, limit)
    if not leaderboard:
        await interaction.response.send_message("No tracked data yet.")
        return

    lines: List[str] = []
    for index, (user_id, seconds) in enumerate(leaderboard, start=1):
        member = interaction.guild.get_member(user_id)
        display_name = member.mention if member else f"<@{user_id}>"
        lines.append(f"{index}. {display_name} - **{format_duration(seconds)}**")

    await interaction.response.send_message("\n".join(lines))


@tree.command(name="rangetime", description="Calculate voice time between two datetimes (Vietnam time, UTC+7).")
@app_commands.describe(
    start="Start datetime (VN time): YYYY-MM-DD HH:MM or ISO8601",
    end="End datetime (VN time): YYYY-MM-DD HH:MM or ISO8601",
    member="Optional member to inspect",
    channel="Optional voice channel filter",
)
async def rangetime(
    interaction: discord.Interaction,
    start: str,
    end: str,
    member: Optional[discord.Member] = None,
    channel: Optional[discord.VoiceChannel] = None,
) -> None:
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    # Acknowledge quickly to avoid Discord's 3-second interaction timeout.
    await interaction.response.defer(thinking=True)

    try:
        start_at = parse_datetime_input(start)
        end_at = parse_datetime_input(end)
    except ValueError:
        await interaction.followup.send(
            "Invalid datetime format. Use `YYYY-MM-DD HH:MM` or ISO8601. Naive time is interpreted as Vietnam time (UTC+7).",
            ephemeral=True,
        )
        return

    if end_at <= start_at:
        await interaction.followup.send("`end` must be later than `start`.", ephemeral=True)
        return

    target = member or interaction.user
    selected_channel_id = channel.id if channel else None

    sessions = db.get_user_sessions_between(
        interaction.guild.id,
        target.id,
        start_at,
        end_at,
        selected_channel_id,
    )

    totals: Dict[int, int] = {}
    for channel_id, session_start, session_end in sessions:
        overlap_start = max(session_start, start_at)
        overlap_end = min(session_end, end_at)
        overlap_seconds = int((overlap_end - overlap_start).total_seconds())
        if overlap_seconds > 0:
            totals[channel_id] = totals.get(channel_id, 0) + overlap_seconds

    key = (interaction.guild.id, target.id)
    live_session = active_sessions.get(key)
    if live_session and (selected_channel_id is None or live_session.channel_id == selected_channel_id):
        live_start = max(live_session.joined_at, start_at)
        live_end = min(utcnow(), end_at)
        live_seconds = int((live_end - live_start).total_seconds())
        if live_seconds > 0:
            totals[live_session.channel_id] = totals.get(live_session.channel_id, 0) + live_seconds

    if not totals:
        await interaction.followup.send(
            f"No tracked voice time for {target.mention} in this range."
        )
        return

    sorted_rows = sorted(totals.items(), key=lambda item: item[1], reverse=True)
    total_seconds = sum(seconds for _, seconds in sorted_rows)

    lines: List[str] = []
    for channel_id, seconds in sorted_rows:
        channel_obj = interaction.guild.get_channel(channel_id)
        channel_name = channel_obj.mention if channel_obj else f"Deleted Channel ({channel_id})"
        lines.append(f"{channel_name}: **{format_duration(seconds)}**")

    details = "\n".join(lines[:20])
    if len(lines) > 20:
        details += f"\n... and {len(lines) - 20} more channels"

    await interaction.followup.send(
        f"Voice time for {target.mention}\n"
        f"Range (Vietnam time): `{to_vn_display(start_at)}` -> `{to_vn_display(end_at)}`\n"
        f"Total: **{format_duration(total_seconds)}**\n\n"
        f"{details}"
    )


@tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    # Ensure users see a response instead of "application did not respond" when exceptions occur.
    print(f"[APP_COMMAND_ERROR] {error}")

    message = "An unexpected error occurred while processing this command. Please try again."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        pass


if __name__ == "__main__":
    try:
        client.run(TOKEN)
    except discord.LoginFailure:
        print("[ERROR] Invalid DISCORD_TOKEN. Please open .env and paste a valid Bot Token from Discord Developer Portal.")
        raise SystemExit(1)
