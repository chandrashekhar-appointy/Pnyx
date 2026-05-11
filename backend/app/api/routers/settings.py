from fastapi import APIRouter, Depends, HTTPException
import logging
from typing import List, Optional

try:
    from ..deps import get_current_user
    from ...schemas.user import User
    from ...schemas.settings import (
        SaveModelConfigRequest,
        SaveTranscriptConfigRequest,
        GetApiKeyRequest,
        UserApiKeySaveRequest,
        UserAIHostSkillRequest,
        UserAIHostSkillResponse,
        UserAIHostSkillGenerateRequest,
        UserAIHostSkillGenerateResponse,
        AIHostStyleItem,
        AIHostStylesListResponse,
        UserAIHostStyleCreateRequest,
        UserAIHostStyleUpdateRequest,
        UserAIHostStyleDefaultRequest,
        UserEncryptionKeySaveRequest,
    )
    from ...db import DatabaseManager
    from ...services.ai_participant import SYSTEM_HOST_SKILLS
    from ...services.ai_participant_skills import parse_skill_markdown
    from ...services.gemini_client import generate_content_text_async
except (ImportError, ValueError):
    from api.deps import get_current_user
    from schemas.user import User
    from schemas.settings import (
        SaveModelConfigRequest,
        SaveTranscriptConfigRequest,
        GetApiKeyRequest,
        UserApiKeySaveRequest,
        UserAIHostSkillRequest,
        UserAIHostSkillResponse,
        UserAIHostSkillGenerateRequest,
        UserAIHostSkillGenerateResponse,
        AIHostStyleItem,
        AIHostStylesListResponse,
        UserAIHostStyleCreateRequest,
        UserAIHostStyleUpdateRequest,
        UserAIHostStyleDefaultRequest,
        UserEncryptionKeySaveRequest,
    )
    from db import DatabaseManager
    from services.ai_participant import SYSTEM_HOST_SKILLS
    from services.ai_participant_skills import parse_skill_markdown
    from services.gemini_client import generate_content_text_async

# Initialize services
db = DatabaseManager()

router = APIRouter()
logger = logging.getLogger(__name__)


def mask_key(key: Optional[str]) -> Optional[str]:
    """Mask an API key for safe display in UI"""
    if not key:
        return None
    if key.startswith("****"):
        return key
    return "****************"  # Fixed masked placeholder


@router.post("/save-model-config")
async def save_model_config(
    request: SaveModelConfigRequest, current_user: User = Depends(get_current_user)
):
    """Save the model configuration"""
    await db.save_model_config(request.provider, request.model, request.whisperModel)
    if request.apiKey is not None:
        # Don't save if it's just the masked placeholder
        if (
            request.apiKey == "****************"
            or request.apiKey == "****"
            or (request.apiKey and "..." in request.apiKey)
        ):
            logger.info(
                f"Skipping save for masked API key (provider: {request.provider})"
            )
        else:
            # Save as personal key for isolation
            await db.save_user_api_key(
                current_user.email, request.provider, request.apiKey
            )
    return {"status": "success", "message": "Model configuration saved successfully"}


@router.get("/get-model-config")
async def get_model_config(current_user: User = Depends(get_current_user)):
    """Get the model configuration"""
    config = await db.get_model_config()
    if config:
        # HOTFIX: Migrate users away from retired models
        retired_models = ["gemini-1.5-pro", "gemini-1.5-flash", "gemini-2.0-flash", "gemini-1.5-pro-latest", "gemini-3-pro-preview", "gemini-3-flash", "gemini-3-flash-preview"]
        if config.get("model", "") in retired_models:
            logger.info(
                f"Migrating retired model {config['model']} to gemini-2.5-flash"
            )
            config["model"] = "gemini-2.5-flash"
            await db.save_model_config(
                config["provider"],
                "gemini-2.5-flash",
                config.get("whisperModel", "large-v3"),
            )

        # Check if user has a personal API key for the provider
        user_key = await db.get_user_api_key(current_user.email, config["provider"])
        if user_key:
            config["apiKey"] = mask_key(user_key)
        else:
            # Fallback to system key
            system_key = await db.get_api_key(config["provider"])
            if system_key:
                config["apiKey"] = mask_key(system_key)
            else:
                # Fallback to Env Var check to satisfy frontend validation
                import os

                provider = config["provider"]
                env_key = None
                if provider == "gemini":
                    env_key = os.getenv("GEMINI_API_KEY")
                elif provider == "openai":
                    env_key = os.getenv("OPENAI_API_KEY")
                elif provider == "groq":
                    env_key = os.getenv("GROQ_API_KEY")
                elif provider == "claude":
                    env_key = os.getenv("ANTHROPIC_API_KEY")
                elif provider == "openrouter":
                    env_key = os.getenv("OPENROUTER_API_KEY")

                if env_key:
                    config["apiKey"] = mask_key("EXISTS")

    return config


@router.get("/get-transcript-config")
async def get_transcript_config(current_user: User = Depends(get_current_user)):
    """Get the current transcript configuration"""
    transcript_config = await db.get_transcript_config()
    if transcript_config:
        transcript_api_key = await db.get_transcript_api_key(
            transcript_config["provider"], user_email=current_user.email
        )
        if transcript_api_key:
            transcript_config["apiKey"] = mask_key(transcript_api_key)
    return transcript_config


@router.post("/save-transcript-config")
async def save_transcript_config(
    request: SaveTranscriptConfigRequest, current_user: User = Depends(get_current_user)
):
    """Save the transcript configuration"""
    await db.save_transcript_config(request.provider, request.model)
    if request.apiKey is not None:
        if (
            request.apiKey == "****************"
            or request.apiKey == "****"
            or (request.apiKey and "..." in request.apiKey)
        ):
            logger.info(
                f"Skipping save for masked transcript API key (provider: {request.provider})"
            )
        else:
            await db.save_user_api_key(
                current_user.email, request.provider, request.apiKey
            )
    return {
        "status": "success",
        "message": "Transcript configuration saved successfully",
    }


@router.post("/get-api-key")
async def get_api_key_api(
    request: GetApiKeyRequest, current_user: User = Depends(get_current_user)
):
    try:
        return await db.get_api_key(request.provider, user_email=current_user.email)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/get-transcript-api-key")
async def get_transcript_api_key_api(
    request: GetApiKeyRequest, current_user: User = Depends(get_current_user)
):
    try:
        return await db.get_transcript_api_key(
            request.provider, user_email=current_user.email
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- User Personal API Keys Endpoints ---


@router.get("/api/user/keys")
async def get_user_keys(current_user: User = Depends(get_current_user)):
    """Get masked API keys for the current user"""
    try:
        return await db.get_user_api_keys(current_user.email)
    except Exception as e:
        logger.error(f"Error fetching user keys: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch keys")


@router.post("/api/user/keys")
async def save_user_key(
    request: UserApiKeySaveRequest, current_user: User = Depends(get_current_user)
):
    """Save/Update an encrypted API key for the current user"""
    try:
        await db.save_user_api_key(
            current_user.email, request.provider, request.api_key
        )
        return {"status": "success", "message": f"API key for {request.provider} saved"}
    except Exception as e:
        logger.error(f"Error saving user key: {e}")
        raise HTTPException(status_code=500, detail="Failed to save key")


@router.delete("/api/user/keys/{provider}")
async def delete_user_key(
    provider: str, current_user: User = Depends(get_current_user)
):
    """Delete an API key for the current user"""
    try:
        await db.delete_user_api_key(current_user.email, provider)
        return {"status": "success", "message": f"API key for {provider} deleted"}
    except Exception as e:
        logger.error(f"Error deleting user key: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete key")


@router.post("/api/user/encryption-key")
async def save_user_encryption_key(
    request: UserEncryptionKeySaveRequest, current_user: User = Depends(get_current_user)
):
    """Save the user's public encryption key (SPKI format)"""
    try:
        await db.save_user_encryption_key(current_user.email, request.public_key)
        return {"status": "success", "message": "Encryption public key saved"}
    except Exception as e:
        logger.error(f"Error saving encryption key: {e}")
        raise HTTPException(status_code=500, detail="Failed to save encryption key")


@router.delete("/api/user/encryption-key")
async def delete_user_encryption_key_api(
    current_user: User = Depends(get_current_user)
):
    """Clear the user's encryption key and disable encryption"""
    try:
        await db.delete_user_encryption_key(current_user.email)
        # Automatically disable encryption when the key is deleted to prevent silent plaintext fallback
        await db.set_user_encryption_enabled(current_user.email, False)
        return {"status": "success", "message": "Encryption key cleared and encryption disabled"}
    except Exception as e:
        logger.error(f"Error clearing encryption key: {e}")
        raise HTTPException(status_code=500, detail="Failed to clear key")


@router.get("/api/user/encryption-status")
async def get_encryption_status(current_user: User = Depends(get_current_user)):
    """Get the current user's encryption enabled status"""
    try:
        enabled = await db.get_user_encryption_enabled(current_user.email)
        return {"enabled": enabled}
    except Exception as e:
        logger.error(f"Error getting encryption status: {e}")
        raise HTTPException(status_code=500, detail="Failed to get encryption status")


@router.post("/api/user/encryption-status")
async def set_encryption_status(
    request: dict, current_user: User = Depends(get_current_user)
):
    """Update the current user's encryption enabled status"""
    enabled = request.get("enabled", False)
    try:
        if enabled:
            # Enforce that the user has a public key uploaded before enabling encryption
            user_info = await db.get_user_credits(current_user.email)
            if not user_info or not user_info.get("encryption_public_key"):
                raise HTTPException(
                    status_code=400, 
                    detail="Cannot enable encryption without a valid public key registered. Please initialize your encryption key pair first."
                )

        await db.set_user_encryption_enabled(current_user.email, enabled)
        return {"status": "success", "enabled": enabled}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting encryption status: {e}")
        raise HTTPException(status_code=500, detail="Failed to set encryption status")


@router.get("/api/user/ai-host-skill", response_model=UserAIHostSkillResponse)
async def get_user_ai_host_skill(current_user: User = Depends(get_current_user)):
    """Get persisted AI host skill profile for current user."""
    try:
        skill = await db.get_user_ai_host_skill(current_user.email)
        if not skill:
            return UserAIHostSkillResponse(
                user_email=current_user.email,
                skill_markdown="",
                is_active=True,
                source="user",
            )
        return UserAIHostSkillResponse(
            user_email=skill["user_email"],
            skill_markdown=skill.get("skill_markdown") or "",
            is_active=bool(skill.get("is_active", True)),
            source="user",
        )
    except Exception as e:
        logger.error(f"Error fetching user ai host skill: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch AI host skill")


@router.post("/api/user/ai-host-skill", response_model=UserAIHostSkillResponse)
async def save_user_ai_host_skill(
    request: UserAIHostSkillRequest, current_user: User = Depends(get_current_user)
):
    """Save/update persisted AI host skill profile for current user."""
    skill_text = (request.skill_markdown or "").strip()
    if len(skill_text) > 20000:
        raise HTTPException(
            status_code=400, detail="AI host skill markdown exceeds max length (20000)"
        )
    try:
        saved = await db.upsert_user_ai_host_skill(
            current_user.email,
            skill_markdown=skill_text,
            is_active=bool(request.is_active),
        )
        return UserAIHostSkillResponse(
            user_email=saved["user_email"],
            skill_markdown=saved.get("skill_markdown") or "",
            is_active=bool(saved.get("is_active", True)),
            source="user",
        )
    except Exception as e:
        logger.error(f"Error saving user ai host skill: {e}")
        raise HTTPException(status_code=500, detail="Failed to save AI host skill")


_SKILL_GENERATOR_SYSTEM_PROMPT = """You convert a non-technical user's plain-English description of how they want their meeting AI assistant to behave into a SHORT, FRIENDLY skill markdown document.

The output must follow this EXACT structure (the parser is strict):

```
---
name: "<short title for this style, max 60 chars>"
description: "<one warm sentence describing what this AI does, max 180 chars>"
---

# Role
<2-4 sentences in plain, warm English describing who the AI is in this meeting and the tone it should take. Speak in second person to the AI: "You are…", "You help…". No jargon, no bullet points here — write it like a short note to a teammate.>

# Goals
1. <Goal 1 — short, plain-English sentence>
2. <Goal 2>
3. <Goal 3>
(2 to 5 goals total)

# Allowed Custom Event Types
- `<event_type_name>`: <Plain English explanation of when to flag this.>
- `<event_type_name>`: <Plain English explanation.>
(2 to 5 event types. Use lowercase_with_underscores for the name.)

# Rules
- <Rule 1 — short do/don't sentence>
- <Rule 2>
- <Rule 3>
(3 to 6 rules total)
```

CRITICAL constraints:
- Output ONLY the markdown above. No preamble, no commentary, no code fences around the whole document.
- Keep section headers EXACTLY as shown ("# Role", "# Goals", "# Allowed Custom Event Types", "# Rules"). Capitalisation matters.
- Frontmatter keys must be exactly `name` and `description`, both quoted.
- Event type names must be lowercase_with_underscores wrapped in single backticks.
- Use plain, warm, encouraging language — this will be shown to non-technical users who should feel comfortable editing it. Avoid technical jargon (no "threshold", "confidence", "callback", etc.).
- The user's intent should genuinely shape what's emitted — don't just paraphrase a generic template."""


_SKILL_GENERATOR_USER_PROMPT_TEMPLATE = """The user described what they want their meeting AI to do:

\"\"\"
{user_prompt}
\"\"\"

Suggested name (optional, may be empty): "{suggested_name}"

Generate the skill markdown now. Remember: output ONLY the markdown, nothing else."""


def _strip_outer_code_fence(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```"):
        # remove first fence line
        first_newline = s.find("\n")
        if first_newline != -1:
            s = s[first_newline + 1 :]
        if s.endswith("```"):
            s = s[: -3]
    return s.strip()


@router.post(
    "/api/user/ai-host-skill/generate-from-prompt",
    response_model=UserAIHostSkillGenerateResponse,
)
async def generate_user_ai_host_skill(
    request: UserAIHostSkillGenerateRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Convert a plain-English description into a structured skill markdown
    document. Lets non-technical users describe how they want their meeting
    assistant to behave without writing the markdown by hand.
    """
    import os

    user_prompt = (request.prompt or "").strip()
    if not user_prompt:
        raise HTTPException(status_code=400, detail="prompt is required")
    if len(user_prompt) > 4000:
        raise HTTPException(
            status_code=400, detail="prompt too long (max 4000 chars)"
        )

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        try:
            api_key = await db.get_api_key("gemini", user_email=current_user.email)
        except Exception:
            api_key = None
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="Gemini API key not configured. Add GEMINI_API_KEY or save a Gemini key in Settings.",
        )

    suggested_name = (request.suggested_name or "").strip()
    user_block = _SKILL_GENERATOR_USER_PROMPT_TEMPLATE.format(
        user_prompt=user_prompt, suggested_name=suggested_name
    )
    model = os.getenv("AI_HOST_SKILL_GENERATOR_MODEL", "gemini-2.5-flash")

    try:
        raw = await generate_content_text_async(
            api_key=api_key,
            model=model,
            contents=user_block,
            config={
                "system_instruction": _SKILL_GENERATOR_SYSTEM_PROMPT,
                "temperature": 0.4,
            },
        )
    except Exception as exc:
        logger.error("Skill generation failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=502, detail=f"Failed to generate skill: {exc}"
        )

    markdown = _strip_outer_code_fence(raw or "")
    if not markdown.lstrip().startswith("---"):
        raise HTTPException(
            status_code=502,
            detail="Generator returned unexpected output (no frontmatter). Try rephrasing your description.",
        )

    parsed = parse_skill_markdown(markdown)
    name = str(parsed.get("name") or "").strip() or suggested_name or "Custom Style"

    if not parsed.get("role") or not parsed.get("rules") or not parsed.get("goals"):
        raise HTTPException(
            status_code=502,
            detail="Generator returned an incomplete document (missing Role/Goals/Rules). Try rephrasing your description.",
        )

    return UserAIHostSkillGenerateResponse(name=name, skill_markdown=markdown)


@router.delete("/api/user/ai-host-skill")
async def delete_user_ai_host_skill(current_user: User = Depends(get_current_user)):
    """Delete persisted AI host skill profile for current user."""
    try:
        await db.delete_user_ai_host_skill(current_user.email)
        return {"status": "success", "message": "AI host skill deleted"}
    except Exception as e:
        logger.error(f"Error deleting user ai host skill: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete AI host skill")


def _system_style_items(default_style_id: str) -> List[AIHostStyleItem]:
    items: List[AIHostStyleItem] = []
    for key, markdown in SYSTEM_HOST_SKILLS.items():
        style_id = f"system:{key}"
        parsed = parse_skill_markdown(markdown)
        items.append(
            AIHostStyleItem(
                id=style_id,
                name=str(parsed.get("name") or key.title()),
                source="system",
                read_only=True,
                is_default=(style_id == default_style_id),
                is_active=True,
                skill_markdown=markdown,
            )
        )
    return items


@router.get("/api/user/ai-host-styles", response_model=AIHostStylesListResponse)
async def list_user_ai_host_styles(current_user: User = Depends(get_current_user)):
    """List system read-only styles + user custom styles with default marker."""
    try:
        default_style_id = await db.get_user_ai_host_default_style_id(current_user.email)
        if not default_style_id:
            default_style_id = "system:facilitator"

        system_items = _system_style_items(default_style_id=default_style_id)
        custom_rows = await db.list_user_ai_host_styles(current_user.email)
        custom_items = [
            AIHostStyleItem(
                id=f"user:{row['id']}",
                name=row.get("name") or "Custom Style",
                source="user",
                read_only=False,
                is_default=(f"user:{row['id']}" == default_style_id),
                is_active=bool(row.get("is_active", True)),
                skill_markdown=row.get("skill_markdown") or "",
            )
            for row in custom_rows
        ]
        return AIHostStylesListResponse(
            styles=[*system_items, *custom_items],
            default_style_id=default_style_id,
        )
    except Exception as e:
        logger.error(f"Error listing ai host styles: {e}")
        raise HTTPException(status_code=500, detail="Failed to list AI host styles")


@router.post("/api/user/ai-host-styles", response_model=AIHostStyleItem)
async def create_user_ai_host_style(
    request: UserAIHostStyleCreateRequest,
    current_user: User = Depends(get_current_user),
):
    try:
        name = (request.name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Style name is required")
        markdown = (request.skill_markdown or "").strip()
        if len(markdown) > 20000:
            raise HTTPException(status_code=400, detail="Skill markdown exceeds max length (20000)")
        created = await db.create_user_ai_host_style(
            user_email=current_user.email,
            name=name,
            skill_markdown=markdown,
            is_active=bool(request.is_active),
        )
        style_id = f"user:{created['id']}"
        if request.set_default:
            await db.set_user_ai_host_default_style_id(current_user.email, style_id)
        return AIHostStyleItem(
            id=style_id,
            name=created.get("name") or "Custom Style",
            source="user",
            read_only=False,
            is_default=bool(request.set_default),
            is_active=bool(created.get("is_active", True)),
            skill_markdown=created.get("skill_markdown") or "",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating ai host style: {e}")
        raise HTTPException(status_code=500, detail="Failed to create AI host style")


@router.put("/api/user/ai-host-styles/{style_id}", response_model=AIHostStyleItem)
async def update_user_ai_host_style(
    style_id: str,
    request: UserAIHostStyleUpdateRequest,
    current_user: User = Depends(get_current_user),
):
    clean_id = (style_id or "").strip()
    if not clean_id.startswith("user:"):
        raise HTTPException(status_code=400, detail="Only user styles can be updated")
    row_id = clean_id.split("user:", 1)[1]
    markdown = request.skill_markdown
    if markdown is not None and len((markdown or "").strip()) > 20000:
        raise HTTPException(status_code=400, detail="Skill markdown exceeds max length (20000)")
    try:
        updated = await db.update_user_ai_host_style(
            user_email=current_user.email,
            style_id=row_id,
            name=request.name,
            skill_markdown=markdown,
            is_active=request.is_active,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Style not found")
        default_style_id = await db.get_user_ai_host_default_style_id(current_user.email)
        return AIHostStyleItem(
            id=clean_id,
            name=updated.get("name") or "Custom Style",
            source="user",
            read_only=False,
            is_default=(default_style_id == clean_id),
            is_active=bool(updated.get("is_active", True)),
            skill_markdown=updated.get("skill_markdown") or "",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating ai host style: {e}")
        raise HTTPException(status_code=500, detail="Failed to update AI host style")


@router.delete("/api/user/ai-host-styles/{style_id}")
async def delete_user_ai_host_style(
    style_id: str, current_user: User = Depends(get_current_user)
):
    clean_id = (style_id or "").strip()
    if not clean_id.startswith("user:"):
        raise HTTPException(status_code=400, detail="Only user styles can be deleted")
    row_id = clean_id.split("user:", 1)[1]
    try:
        deleted = await db.delete_user_ai_host_style(current_user.email, row_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Style not found")
        default_style_id = await db.get_user_ai_host_default_style_id(current_user.email)
        if default_style_id == clean_id:
            await db.set_user_ai_host_default_style_id(current_user.email, "system:facilitator")
        return {"status": "success", "message": "AI host style deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting ai host style: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete AI host style")


@router.post("/api/user/ai-host-styles/default")
async def set_user_ai_host_default_style(
    request: UserAIHostStyleDefaultRequest,
    current_user: User = Depends(get_current_user),
):
    style_id = (request.style_id or "").strip()
    if not style_id:
        raise HTTPException(status_code=400, detail="style_id is required")
    if style_id.startswith("system:"):
        sys_key = style_id.split("system:", 1)[1]
        if sys_key not in SYSTEM_HOST_SKILLS:
            raise HTTPException(status_code=400, detail="Invalid system style")
    elif style_id.startswith("user:"):
        row_id = style_id.split("user:", 1)[1]
        row = await db.get_user_ai_host_style_by_id(current_user.email, row_id)
        if not row:
            raise HTTPException(status_code=404, detail="User style not found")
    else:
        raise HTTPException(status_code=400, detail="Invalid style_id format")
    try:
        saved = await db.set_user_ai_host_default_style_id(current_user.email, style_id)
        return {"status": "success", "default_style_id": saved}
    except Exception as e:
        logger.error(f"Error setting default ai host style: {e}")
        raise HTTPException(status_code=500, detail="Failed to set default AI host style")
