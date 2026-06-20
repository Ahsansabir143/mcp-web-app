class StreamNames:
    """All Redis stream names.  Never construct these strings outside this module."""

    RAW = "stream:binance:raw"
    NORMALIZED = "stream:binance:normalized"
    ANALYTICS_DERIVED = "stream:analytics:derived"
    STRATEGY_INTENTS = "stream:strategy:intents"
    EXECUTION_EVENTS = "stream:execution:events"
    MCP_AUDIT = "stream:mcp:audit"

    @classmethod
    def all(cls) -> list[str]:
        return [
            cls.RAW,
            cls.NORMALIZED,
            cls.ANALYTICS_DERIVED,
            cls.STRATEGY_INTENTS,
            cls.EXECUTION_EVENTS,
            cls.MCP_AUDIT,
        ]
