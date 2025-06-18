# Constraint-Aware Production Scheduling AI

Builds constraint-aware production schedules with an optimizer, simulation, and grounded AI assistant.

## Features

- Constraint-aware scheduling with OR-Tools CP-SAT and a deterministic FCFS baseline.
- Models routing alternatives, parallel groups, transfer times, batch-aware run time, and due dates.
- Compares weighted tardiness, makespan, setup, overtime, cost, flow, and schedule stability.
- Monte Carlo disruption simulation and what-if scenarios with paired comparisons.
- Grounded AI assistant for explaining schedules and benchmark results.
- React dashboard with Gantt charts, optimizer comparison, and scenario controls.

## Stack

Python, Node.js, FastAPI, Uvicorn, Pydantic, SQLAlchemy, HTTPX, React, TypeScript, Vite

## Clone

```bash
git clone https://github.com/sanjaymouraly/constraint-aware-production-scheduling-ai.git
cd constraint-aware-production-scheduling-ai
```

## Setup

```bash
# The project ships a Docker Compose setup.
docker compose up --build
```

See `docs/` or the compose file for environment variables and ports.

## Run

```bash
docker compose up --build
```

## Notes

- Sample data, when included, is synthetic.

## License

MIT © 2025 [Sanjay Mouraly](https://github.com/sanjaymouraly)
