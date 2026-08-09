from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Farm Irrigation API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

valve_state = {
    1: "OFF"
}

pending_command = None


@app.get("/")
def root():
    return {
        "status": "online",
        "service": "Farm Irrigation API"
    }


@app.get("/api/valves")
def get_valves():
    return {
        "valves": [
            {
                "id": 1,
                "name": "Valve 1",
                "state": valve_state[1]
            }
        ]
    }


@app.post("/api/valves/{valve_id}/on")
def valve_on(valve_id: int):
    global pending_command

    if valve_id != 1:
        return {"error": "Valve not found"}

    pending_command = {
        "valve_id": 1,
        "command": "ON"
    }

    return {
        "status": "queued",
        "valve_id": 1,
        "command": "ON"
    }


@app.post("/api/valves/{valve_id}/off")
def valve_off(valve_id: int):
    global pending_command

    if valve_id != 1:
        return {"error": "Valve not found"}

    pending_command = {
        "valve_id": 1,
        "command": "OFF"
    }

    return {
        "status": "queued",
        "valve_id": 1,
        "command": "OFF"
    }


@app.get("/api/gateway/command")
def gateway_command():
    global pending_command

    command = pending_command

    if command is not None:
        pending_command = None

    return {
        "command": command
    }


@app.post("/api/gateway/ack")
def gateway_ack(valve_id: int, command: str):
    if valve_id not in valve_state:
        return {"error": "Valve not found"}

    valve_state[valve_id] = command

    return {
        "status": "acknowledged",
        "valve_id": valve_id,
        "state": command
    }