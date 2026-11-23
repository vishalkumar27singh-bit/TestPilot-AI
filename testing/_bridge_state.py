"""Shared state between the executor and the pytest bridge (kept in its own module so it is a single
instance no matter how pytest imports the bridge file)."""
STATE: dict = {"ids": [], "run_one": None}
