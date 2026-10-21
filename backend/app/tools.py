"""Registered tools available to the agent (local + browser)."""
from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, ValidationError, field_validator

if TYPE_CHECKING:
    from .browser.session import BrowserService


class ToolError(Exception):
    """A controlled failure that can be returned to the model and UI."""


class TimeArguments(BaseModel):
    timezone: str = Field(default="Asia/Calcutta", max_length=80)


class CalculateArguments(BaseModel):
    operation: Literal["add", "subtract", "multiply", "divide"]
    left: float
    right: float

    @field_validator("left", "right")
    @classmethod
    def finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("must be a finite number")
        return value


class DemoApprovalArguments(BaseModel):
    message: str = Field(min_length=1, max_length=200)


ToolExecutor = Callable[[BaseModel], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    arguments_model: type[BaseModel]
    permission: Literal["read_only", "approval_required"]
    timeout_seconds: float
    execute: ToolExecutor

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.arguments_model.model_json_schema(),
            },
        }


async def get_current_time(arguments: TimeArguments) -> dict[str, Any]:
    try:
        zone = ZoneInfo(arguments.timezone)
    except ZoneInfoNotFoundError as exc:
        raise ToolError("Unknown timezone. Use an IANA timezone such as Asia/Calcutta.") from exc
    now = datetime.now(zone)
    return {"timezone": arguments.timezone, "datetime": now.isoformat(), "unix_timestamp": int(now.timestamp())}


async def calculate(arguments: CalculateArguments) -> dict[str, Any]:
    operations = {
        "add": lambda: arguments.left + arguments.right,
        "subtract": lambda: arguments.left - arguments.right,
        "multiply": lambda: arguments.left * arguments.right,
        "divide": lambda: arguments.left / arguments.right,
    }
    if arguments.operation == "divide" and arguments.right == 0:
        raise ToolError("Division by zero is not allowed.")
    result = operations[arguments.operation]()
    if not math.isfinite(result):
        raise ToolError("The calculation result is not finite.")
    return {"operation": arguments.operation, "left": arguments.left, "right": arguments.right, "result": result}


async def demo_request_approval(arguments: DemoApprovalArguments) -> dict[str, Any]:
    # Deliberately local and side-effect free. It demonstrates only the approval state machine.
    return {"simulated": True, "message": arguments.message, "outcome": "The approved demo action was simulated locally."}


class ToolRegistry:
    def __init__(
        self,
        default_timezone: str,
        timeout_seconds: float,
        max_argument_bytes: int,
        max_result_bytes: int,
        browser: BrowserService | None = None,
        browser_timeout_seconds: float = 30.0,
    ):
        self.default_timezone = default_timezone
        self.max_argument_bytes = max_argument_bytes
        self.max_result_bytes = max_result_bytes
        self._tools: dict[str, ToolSpec] = {}
        self.register(ToolSpec("get_current_time", "Get the current system time in an IANA timezone.", TimeArguments, "read_only", timeout_seconds, get_current_time))
        self.register(ToolSpec("calculate", "Perform one basic arithmetic operation using two finite numbers.", CalculateArguments, "read_only", timeout_seconds, calculate))
        self.register(ToolSpec("demo_request_approval", "Run a harmless local demonstration action only after explicit user approval.", DemoApprovalArguments, "approval_required", timeout_seconds, demo_request_approval))
        if browser is not None:
            from .browser.tool_handlers import register_browser_tools

            register_browser_tools(self, browser, browser_timeout_seconds)

    def register(self, tool: ToolSpec) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolSpec:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(f"Unknown tool: {name}")
        return tool

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.openai_schema() for tool in self._tools.values()]

    def validate(self, name: str, raw_arguments: str) -> tuple[ToolSpec, BaseModel, dict[str, Any]]:
        if len(raw_arguments.encode("utf-8")) > self.max_argument_bytes:
            raise ToolError("Tool arguments exceed the configured size limit.")
        tool = self.get(name)
        try:
            parsed = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            raise ToolError("Tool arguments are not valid JSON.") from exc
        if not isinstance(parsed, dict):
            raise ToolError("Tool arguments must be a JSON object.")
        if name == "get_current_time" and "timezone" not in parsed:
            parsed["timezone"] = self.default_timezone
        try:
            arguments = tool.arguments_model.model_validate(parsed)
        except ValidationError as exc:
            raise ToolError("Invalid tool arguments: " + exc.errors()[0]["msg"]) from exc
        return tool, arguments, arguments.model_dump(mode="json")

    async def execute(self, tool: ToolSpec, arguments: BaseModel) -> dict[str, Any]:
        try:
            result = await asyncio.wait_for(tool.execute(arguments), timeout=tool.timeout_seconds)
        except TimeoutError as exc:
            raise ToolError("Tool execution timed out.") from exc
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError("Tool execution failed.") from exc
        encoded = json.dumps(result, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > self.max_result_bytes:
            raise ToolError("Tool result exceeds the configured size limit.")
        return result
