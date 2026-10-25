# AI Assistance Layer

AI is an assistant layer for product-library text only:

```text
product data + NAS asset index
-> title or attribute suggestion
-> validation
-> ai_outputs audit record
-> human explicitly applies result
```

AI does not save product fields automatically and does not export files.

## Title Suggestions

```text
POST /api/products/{product_id}/ai/title-suggestion
GET  /api/products/{product_id}/ai/outputs?output_type=title_suggestion
```

The endpoint reads the product, assets, and SKUs, builds a prompt, validates the suggestion, stores an audit record, and returns `policy.auto_apply=false`.

## Attribute Suggestions

```text
POST /api/products/{product_id}/ai/attribute-suggestion
GET  /api/products/{product_id}/ai/outputs?output_type=attribute_suggestion
```

The endpoint returns approved product-library fields only and stores the suggestion in `ai_outputs`.

## DeepSeek Configuration

Live calls are disabled by default.

```powershell
$env:DEEPSEEK_BASE_URL = "https://api.deepseek.com"
$env:DEEPSEEK_MODEL = "deepseek-chat"
$env:DEEPSEEK_ENABLE_LIVE = "0"
```

Set both `DEEPSEEK_API_KEY` and `DEEPSEEK_ENABLE_LIVE=1` to enable live suggestions.

## Safety Rules

- Never put API keys in frontend JavaScript.
- Never auto-apply AI output.
- Never auto-export from an AI response.
- Always validate generated text before saving it to the product library.
