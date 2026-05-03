openai.BadRequestError: Error code: 400 - {'error': {'message': 'The supported API model names are deepseek-v4-pro or deepseek-v4-flash, but you passed deepseek-v4-flash:none.', 'type': 'invalid_request_error', 'param': None, 'code': 'invalid_request_error'}}

期望行为：selector:effort应当被正确解析。none作为reasoning effort传入llm api.

