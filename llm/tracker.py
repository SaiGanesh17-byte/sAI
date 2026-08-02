import time

class TokenTracker:
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(TokenTracker, cls).__new__(cls, *args, **kwargs)
            cls._instance.input_tokens = 0
            cls._instance.output_tokens = 0
            cls._instance.calls_count = 0
            cls._instance.start_time = time.time()
        return cls._instance
        
    def reset(self):
        self.input_tokens = 0
        self.output_tokens = 0
        self.calls_count = 0
        self.start_time = time.time()
        
    def add(self, prompt_tokens: int, completion_tokens: int):
        if prompt_tokens:
            self.input_tokens += prompt_tokens
        if completion_tokens:
            self.output_tokens += completion_tokens
        self.calls_count += 1

    @property
    def elapsed_time(self) -> float:
        return time.time() - self.start_time

    @property
    def speed(self) -> float:
        elapsed = self.elapsed_time
        if elapsed > 0.1:
            return self.output_tokens / elapsed
        return 0.0

token_tracker = TokenTracker()
