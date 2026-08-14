from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

app = FastAPI(title="Farm Irrigation API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# Database
# ============================================================

DB_PATH = Path(__file__).resolve().parent / "irrigation.db"

GATEWAY_TIMEOUT_SECONDS = 15


def get_db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def current_time():
    return datetime.now(timezone.utc).isoformat()


def parse_time(value):
    if not value:
        return None

    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def gateway_is_online(last_seen):
    if not last_seen:
        return False

    last_seen_time = parse_time(last_seen)

    if last_seen_time is None:
        return False

    elapsed = (
        datetime.now(timezone.utc) -
        last_seen_time
    ).total_seconds()

    return elapsed <= GATEWAY_TIMEOUT_SECONDS


def initialize_database():

    connection = get_db()

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS valves (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            desired_state TEXT NOT NULL DEFAULT 'CLOSED',
            actual_state TEXT NOT NULL DEFAULT 'UNKNOWN',
            last_command TEXT,
            last_command_time TEXT,
            last_update_time TEXT
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS gateway (
            id INTEGER PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'OFFLINE',
            last_seen TEXT,
            restart_count INTEGER NOT NULL DEFAULT 0
        )
        """
    )

    connection.execute(
        """
        INSERT OR IGNORE INTO valves
        (
            id,
            name,
            desired_state,
            actual_state,
            last_command,
            last_command_time,
            last_update_time
        )
        VALUES
        (
            1,
            'Valve 1',
            'CLOSED',
            'UNKNOWN',
            NULL,
            NULL,
            ?
        )
        """,
        (current_time(),),
    )

    connection.execute(
        """
        INSERT OR IGNORE INTO gateway
        (
            id,
            status,
            last_seen,
            restart_count
        )
        VALUES
        (
            1,
            'OFFLINE',
            NULL,
            0
        )
        """
    )

    connection.commit()
    connection.close()


initialize_database()


# ============================================================
# Root
# ============================================================

@app.get("/")
def root():

    return {
        "status": "online",
        "service": "Farm Irrigation API"
    }


# ============================================================
# Get gateway information
# ============================================================

def get_gateway_info(connection):

    gateway = connection.execute(
        """
        SELECT
            status,
            last_seen,
            restart_count
        FROM gateway
        WHERE id = 1
        """
    ).fetchone()

    if gateway is None:
        return {
            "status": "OFFLINE",
            "last_seen": None,
            "restart_count": 0
        }

    online = gateway_is_online(
        gateway["last_seen"]
    )

    status = (
        "ONLINE"
        if online
        else "OFFLINE"
    )

    # Keep database status synchronized.
    connection.execute(
        """
        UPDATE gateway
        SET status = ?
        WHERE id = 1
        """,
        (status,),
    )

    return {
        "status": status,
        "last_seen": gateway["last_seen"],
        "restart_count": gateway["restart_count"]
    }


# ============================================================
# Get all valves
# ============================================================

@app.get("/api/valves")
def get_valves():

    connection = get_db()

    gateway = get_gateway_info(connection)

    rows = connection.execute(
        """
        SELECT
            id,
            name,
            desired_state,
            actual_state,
            last_command,
            last_command_time,
            last_update_time
        FROM valves
        ORDER BY id
        """
    ).fetchall()

    # If gateway is offline, physical state cannot
    # be confirmed.
    if gateway["status"] == "OFFLINE":

        for row in rows:

            connection.execute(
                """
                UPDATE valves
                SET actual_state = 'UNKNOWN'
                WHERE id = ?
                """,
                (row["id"],),
            )

    connection.commit()

    # Read again after possible updates.
    rows = connection.execute(
        """
        SELECT
            id,
            name,
            desired_state,
            actual_state,
            last_command,
            last_command_time,
            last_update_time
        FROM valves
        ORDER BY id
        """
    ).fetchall()

    connection.close()

    return {
        "gateway": gateway,
        "valves": [dict(row) for row in rows]
    }


# ============================================================
# Get one valve
# ============================================================

@app.get("/api/valves/{valve_id}")
def get_valve(valve_id: int):

    connection = get_db()

    gateway = get_gateway_info(connection)

    row = connection.execute(
        """
        SELECT
            id,
            name,
            desired_state,
            actual_state,
            last_command,
            last_command_time,
            last_update_time
        FROM valves
        WHERE id = ?
        """,
        (valve_id,),
    ).fetchone()

    if row is None:

        connection.close()

        raise HTTPException(
            status_code=404,
            detail="Valve not found"
        )

    actual_state = row["actual_state"]

    if gateway["status"] == "OFFLINE":

        actual_state = "UNKNOWN"

        connection.execute(
            """
            UPDATE valves
            SET actual_state = 'UNKNOWN'
            WHERE id = ?
            """,
            (valve_id,),
        )

        connection.commit()

    connection.close()

    result = dict(row)

    result["actual_state"] = actual_state

    result["gateway_status"] = (
        gateway["status"]
    )

    result["gateway_last_seen"] = (
        gateway["last_seen"]
    )

    return result


# ============================================================
# Turn valve ON
# ============================================================

@app.post("/api/valves/{valve_id}/on")
def valve_on(valve_id: int):

    connection = get_db()

    valve = connection.execute(
        "SELECT id FROM valves WHERE id = ?",
        (valve_id,),
    ).fetchone()

    if valve is None:

        connection.close()

        raise HTTPException(
            status_code=404,
            detail="Valve not found"
        )

    now = current_time()

    connection.execute(
        """
        UPDATE valves
        SET
            desired_state = 'OPEN',
            actual_state = 'OPENING',
            last_command = 'ON',
            last_command_time = ?,
            last_update_time = ?
        WHERE id = ?
        """,
        (
            now,
            now,
            valve_id
        ),
    )

    connection.commit()
    connection.close()

    return {
        "status": "queued",
        "valve_id": valve_id,
        "command": "ON",
        "desired_state": "OPEN",
        "actual_state": "OPENING"
    }


# ============================================================
# Turn valve OFF
# ============================================================

@app.post("/api/valves/{valve_id}/off")
def valve_off(valve_id: int):

    connection = get_db()

    valve = connection.execute(
        "SELECT id FROM valves WHERE id = ?",
        (valve_id,),
    ).fetchone()

    if valve is None:

        connection.close()

        raise HTTPException(
            status_code=404,
            detail="Valve not found"
        )

    now = current_time()

    connection.execute(
        """
        UPDATE valves
        SET
            desired_state = 'CLOSED',
            actual_state = 'CLOSING',
            last_command = 'OFF',
            last_command_time = ?,
            last_update_time = ?
        WHERE id = ?
        """,
        (
            now,
            now,
            valve_id
        ),
    )

    connection.commit()
    connection.close()

    return {
        "status": "queued",
        "valve_id": valve_id,
        "command": "OFF",
        "desired_state": "CLOSED",
        "actual_state": "CLOSING"
    }


# ============================================================
# ESP32 Gateway asks for command
# ============================================================

@app.get("/api/gateway/command")
def gateway_command():

    connection = get_db()

    now = current_time()

    # Gateway heartbeat.
    connection.execute(
        """
        UPDATE gateway
        SET
            status = 'ONLINE',
            last_seen = ?
        WHERE id = 1
        """,
        (now,),
    )

    # Find a command where desired state differs
    # from the last confirmed actual state.
    row = connection.execute(
        """
        SELECT
            id,
            desired_state,
            actual_state,
            last_command
        FROM valves
        WHERE desired_state != actual_state
        ORDER BY id
        LIMIT 1
        """
    ).fetchone()

    connection.commit()
    connection.close()

    if row is None:

        return {
            "command": None
        }

    return {
        "command": {
            "valve_id": row["id"],
            "command": row["last_command"]
        }
    }


# ============================================================
# ESP32 Gateway acknowledgement
# ============================================================

@app.post("/api/gateway/ack")
def gateway_ack(
    valve_id: int,
    command: str
):

    command = command.upper()

    if command not in ("ON", "OFF"):

        raise HTTPException(
            status_code=400,
            detail="Command must be ON or OFF"
        )

    connection = get_db()

    valve = connection.execute(
        "SELECT id FROM valves WHERE id = ?",
        (valve_id,),
    ).fetchone()

    if valve is None:

        connection.close()

        raise HTTPException(
            status_code=404,
            detail="Valve not found"
        )

    actual_state = (
        "OPEN"
        if command == "ON"
        else "CLOSED"
    )

    now = current_time()

    connection.execute(
        """
        UPDATE valves
        SET
            actual_state = ?,
            last_update_time = ?
        WHERE id = ?
        """,
        (
            actual_state,
            now,
            valve_id
        ),
    )

    connection.execute(
        """
        UPDATE gateway
        SET
            status = 'ONLINE',
            last_seen = ?
        WHERE id = 1
        """,
        (now,),
    )

    connection.commit()
    connection.close()

    return {
        "status": "acknowledged",
        "valve_id": valve_id,
        "actual_state": actual_state
    }


# ============================================================
# Gateway status
# ============================================================

@app.get("/api/gateway/status")
def gateway_status():

    connection = get_db()

    gateway = get_gateway_info(
        connection
    )

    connection.commit()
    connection.close()

    return gateway