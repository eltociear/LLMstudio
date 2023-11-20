import time
import uuid
from typing import Any, AsyncGenerator, Dict, Optional, Tuple, Union

import tiktoken
from anthropic import Anthropic
from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from tokenizers import Tokenizer


class ChatRequest(BaseModel):
    api_key: Optional[str] = None
    model: str
    chat_input: str
    parameters: Optional[BaseModel] = None
    is_stream: Optional[bool] = False
    has_end_token: Optional[bool] = False


class Provider:
    END_TOKEN = "<END_TOKEN>"

    def __init__(self, config):
        self.config = config
        self.tokenizer: Tokenizer = self._get_tokenizer()

    async def chat(
        self, chat_request: ChatRequest
    ) -> Union[StreamingResponse, JSONResponse]:
        """Makes a chat connection with the provider's API"""
        if chat_request.model not in self.config.models:
            raise HTTPException(
                status_code=400,
                detail=f"Model {chat_request.model} is not supported by {self.config.name}",
            )

    async def handle_response(
        self, request: ChatRequest, response: AsyncGenerator, start_time: float
    ) -> AsyncGenerator[str, None]:
        """Handles the response from the provider's API"""

    def generate_response(
        self,
        request: ChatRequest,
        chat_output: str,
        usage: Dict[str, Any],
        metrics: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Generates a complete response with metrics"""
        return {
            "id": str(uuid.uuid4()),
            "chat_input": request.chat_input,
            "chat_output": chat_output,
            "timestamp": time.time(),
            "model": request.model,
            "usage": usage,
            "metrics": metrics,
            "parameters": request.parameters.model_dump(),
        }

    def calculate_metrics(
        self,
        start_time: float,
        end_time: float,
        first_token_time: float,
        token_times: Tuple[float, ...],
        token_count: int,
    ) -> Dict[str, Any]:
        """Calculates metrics based on token times and output"""
        total_time = end_time - start_time
        return {
            "latency": total_time,
            "time_to_first_token": first_token_time - start_time,
            "inter_token_latency": sum(token_times) / len(token_times),
            "tokens_per_second": token_count / total_time,
        }

    def calculate_usage(self, input: str, output: str, model: str) -> Dict[str, Any]:
        """Calculates usage based on tokens"""
        model_config = self.config.models[model]
        input_tokens = len(self.tokenizer.encode(input))
        output_tokens = len(self.tokenizer.encode(output))

        input_cost = model_config.input_token_cost * input_tokens
        output_cost = model_config.output_token_cost * output_tokens

        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cost": input_cost + output_cost,
        }

    def get_end_token_string(
        self, usage: Dict[str, Any], metrics: Dict[str, Any]
    ) -> str:
        return f"{self.END_TOKEN},input_tokens={usage['input_tokens']},output_tokens={usage['output_tokens']},cost={usage['cost']},latency={metrics['latency']:.5f},time_to_first_token={metrics['time_to_first_token']:.5f},inter_token_latency={metrics['inter_token_latency']:.5f},tokens_per_second={metrics['tokens_per_second']:.2f}"

    def _get_tokenizer(self) -> Tokenizer:
        return {
            "anthropic": Anthropic().get_tokenizer(),
            "cohere": Tokenizer.from_pretrained("Cohere/command-nightly"),
        }.get(self.config.provider, tiktoken.get_encoding("cl100k_base"))
