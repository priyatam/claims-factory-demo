# Vehicle claims

One damaged-vehicle photo, uploaded or given as a URL, returns the make, model, colour, a damage summary, and a rough repair-cost range. An adjuster reviews that result and authorizes payment.

`docs/architecture.md` is the architecture note.

`docs/requirements.md` is the requirements note.

## Installation

### Local

1. Create the virtualenv:
  ```sh
   uv venv
  ```
2. Install dependencies:
  ```sh
   uv sync
  ```
3. Set `ANTHROPIC_API_KEY`. The model is `claude-sonnet-5` (`ANTHROPIC_MODEL` in `.env.example`; that is also the default when the variable is unset):
  ```sh
   export ANTHROPIC_API_KEY=your-key
  ```
4. Start the app. It listens on `127.0.0.1:8080`:
  ```sh
   uv run python -m claims.web
  ```
5. Open [http://127.0.0.1:8080](http://127.0.0.1:8080) in a browser. Upload a JPEG, PNG, or WebP, or paste an image URL. A private URL is refused. The photo is not stored.



### AWS AgentCore

TODO