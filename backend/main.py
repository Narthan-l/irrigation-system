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


def get_db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def current_time():
    return datetime.now(timezone.utc).isoformat()


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

    # Create Valve 1 if it does not exist.
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

    # Create gateway record if it does not exist.
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
# Get valve status
# ============================================================

@app.get("/api/valves")
def get_valves():
    connection = get_db()

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
        "valves": [dict(row) for row in rows]
    }


# ============================================================
# Get one valve
# ============================================================

@app.get("/api/valves/{valve_id}")
def get_valve(valve_id: int):
    connection = get_db()

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

    connection.close()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Valve not found"
        )

    return dict(row)


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
        (now, now, valve_id),
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
        (now, now, valve_id),
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

    # Mark gateway as online.
    now = current_time()

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

    # Find a valve where desired state differs from actual state.
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

    command = {
        "valve_id": row["id"],
        "command": row["last_command"]
    }

    return {
        "command": command
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
            valve_id,
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

    row = connection.execute(
        """
        SELECT
            status,
            last_seen,
            restart_count
        FROM gateway
        WHERE id = 1
        """
    ).fetchone()

    connection.close()

    return dict(row)