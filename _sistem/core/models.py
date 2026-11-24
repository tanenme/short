"""Centralized Pydantic data contracts (Single Source of Truth) for the Content Studio."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class EpisodeStatus(str, Enum):
    IDEA = "idea"
    RESEARCHED = "researched"
    SCRIPTED = "scripted"
    SOURCED = "sourced"
    RISK_PASSED = "risk_passed"
    VO_READY = "vo_ready"
    EDITING = "editing"
    QC_PASSED = "qc_passed"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    ANALYZED = "analyzed"


class VoiceConfig(BaseModel):
    engine: str = "eleven_v4"
    voice_id: str = ""
    voice_name: str = ""
    language: str = "id"
    stability: float = Field(default=0.40, ge=0.0, le=1.0)
    similarity: float = Field(default=0.80, ge=0.0, le=1.0)
    median_wpm: int = Field(default=131, ge=60, le=220)
    require_audio_tags: bool = True


class PacingRules(BaseModel):
    min_words: int = 40
    max_words: int = 55
    hard_max_words: int = 60
    min_duration_s: float = 19.0
    max_duration_s: float = 25.0
    max_words_per_sentence: int = 12
    target_clip_count: int = 10
    min_clip_duration_s: float = 1.3
    max_clip_duration_s: float = 5.0


class VisualStyle(BaseModel):
    width: int = 1080
    height: int = 1920
    fps: int = 30
    font_name: str = "Arial Black"
    font_size: int = 104
    primary_color_hex: str = "#FFFFFF"
    highlight_color_hex: str = "#FFDD00"
    outline_color_hex: str = "#000000"
    target_lufs: float = -10.8
    bgm_volume_db: float = -8.0
    sfx_volume_db: float = -3.0
    zoom_punch_min: float = 1.06
    zoom_punch_max: float = 1.12
    subtitle_mode: Literal["squooshify_white_3d", "rsvp_1word", "grouped"] = "squooshify_white_3d"
    subtitle_margin_v: int = 440
    caption_lead_s: float = 0.08
    framing_mode: Literal["auto_blur_canvas", "crop_9_16"] = "auto_blur_canvas"
    include_top_callouts: bool = False
    cta_bg_blur: bool = True
    vo_presence_eq: bool = True
    transition_style: Literal["squooshify_v37", "squooshify_v33", "xfade_classic", "hard_cut"] = "squooshify_v37"
    bottom_readability_grad: bool = False
    vector_heart_cta: bool = True
    opening_radial_blur: bool = True
    cta_style: Literal["heart_pill", "gamified_321", "reward_pop"] = "heart_pill"
    adaptive_proof_pacing: bool = True
    intra_unit_split: bool = True
    selective_speed_up: bool = True
    max_animal_speed: float = 1.35
    vo_zero_gap: bool = True
    vo_tempo_boost: float = 1.07
    vo_silence_db: float = -32.0
    vo_min_silence_s: float = 0.10
    cut_on_word_lock: bool = True
    word_onset_lead_s: float = 0.033
    max_flash_transitions: int = 1
    vo_pivot_pause_s: float = 0.26
    bgm_vacuum_max_events: int = 2
    protect_payoff_hold: bool = True
    suppress_rsvp_on_overlay: bool = True
    clip_safety_margin_s: float = 0.10
    # v3.8 retention parameters (benchmarked against 5 Squooshify Shorts)
    hook_max_first_shot_s: float = 1.20
    max_proof_hold_s: float = 2.80
    bgm_from_start: bool = True
    master_true_peak_db: float = -1.00
    sfx_max_events: int = 6
    sfx_min_spacing_s: float = 1.10
    rsvp_min_hold_s: float = 0.34
    framing_fg_ratio: float = 0.80
    blur_canvas_max_ar: float = 1.15


class YppSafetyRules(BaseModel):
    min_original_vo_ratio: float = 0.70
    max_source_clip_s: float = 5.0
    min_transformations_per_clip: int = 2
    min_tier1_fact_sources: int = 2
    max_clips_per_creator_per_week: int = 1
    forbid_nc_nd_licenses: bool = True
    forbid_second_cta_at_end: bool = True
    min_cleanliness_score: int = 70


class CtaRules(BaseModel):
    template_id: str = "template_b"
    pattern: str = "Tapi kalau kamu suka {subject}, Subscribe ya."
    window_start_s: float = 2.0
    window_end_s: float = 8.5


class ScheduleConfig(BaseModel):
    timezone: str = "Asia/Jakarta"
    daily_slots: List[str] = Field(default_factory=lambda: ["06:30", "12:30", "19:30"])
    target_videos_per_day: int = 3


class ChannelProfile(BaseModel):
    channel_id: str
    display_name: str
    folder_name: str
    niche: str
    status: Literal["active", "validation", "planned", "paused"] = "planned"
    focus_subjects: List[str] = Field(default_factory=list)
    competitors: List[str] = Field(default_factory=list)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    pacing: PacingRules = Field(default_factory=PacingRules)
    visual: VisualStyle = Field(default_factory=VisualStyle)
    ypp: YppSafetyRules = Field(default_factory=YppSafetyRules)
    cta: CtaRules = Field(default_factory=CtaRules)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)


class FactSource(BaseModel):
    title: str
    publisher: str = ""
    url: str = ""
    claim_verified: str = ""
    tier: Literal["tier1", "tier2", "unverified"] = "tier1"


class WordTimestamp(BaseModel):
    word: str
    start: float
    end: float


class ClipAsset(BaseModel):
    filename: str
    source_platform: str = "unknown"
    source_url: str = ""
    creator: str = ""
    license: str = "UNKNOWN"
    reusable_safe: bool = False
    cleanliness_status: Literal["BERSIH", "OVERLAY_RINGAN", "BANYAK_OVERLAY", "KOTOR", "UNSCANNED"] = "UNSCANNED"
    cleanliness_score: Optional[int] = None
    subject_action: str = ""
    duration_s: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    sha256: str = ""


class SceneBeat(BaseModel):
    index: int
    brief_start_s: float
    brief_end_s: float
    synced_start_s: Optional[float] = None
    synced_end_s: Optional[float] = None
    visual_desc: str = ""
    vo_line: str = ""
    screen_text: str = ""
    sfx_cue: str = ""
    highlight_words: List[str] = Field(default_factory=list)
    assigned_clip: Optional[str] = None
    transform_ops: List[str] = Field(default_factory=list)


class ScriptSpec(BaseModel):
    hook_text: str = ""
    vo_full_text: str = ""
    word_count: int = 0
    estimated_duration_s: float = 0.0
    audio_tags: List[str] = Field(default_factory=list)
    highlight_words: List[str] = Field(default_factory=list)
    cta_template: str = ""


class UploadPackage(BaseModel):
    titles: List[str] = Field(default_factory=list)
    description: str = ""
    tags: List[str] = Field(default_factory=list)
    scheduled_slot: str = ""
    cover_frame_s: Optional[float] = None
    cover_text: str = ""


class QcCheckItem(BaseModel):
    code: str
    category: Literal["script", "factcheck", "assets", "ypp", "render", "structure"]
    severity: Literal["PASS", "WARN", "FAIL"]
    message: str


class QcReport(BaseModel):
    channel_id: str
    episode_id: str
    checked_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    passed: bool
    score: int = Field(ge=0, le=100)
    checks: List[QcCheckItem] = Field(default_factory=list)

    @property
    def fail_count(self) -> int:
        return sum(1 for c in self.checks if c.severity == "FAIL")

    @property
    def warn_count(self) -> int:
        return sum(1 for c in self.checks if c.severity == "WARN")


class EpisodeManifest(BaseModel):
    """Single Source of Truth for one Short episode (`episode.json`)."""

    schema_version: str = "1.0.0"
    episode_id: str
    channel_id: str
    title: str
    subject: str = ""
    status: EpisodeStatus = EpisodeStatus.IDEA
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    script: ScriptSpec = Field(default_factory=ScriptSpec)
    fact_sources: List[FactSource] = Field(default_factory=list)
    scenes: List[SceneBeat] = Field(default_factory=list)
    clips: List[ClipAsset] = Field(default_factory=list)
    vo_file: Optional[str] = None
    words_alignment: List[WordTimestamp] = Field(default_factory=list)
    upload: UploadPackage = Field(default_factory=UploadPackage)
    qc_report: Optional[QcReport] = None
    extra: Dict[str, object] = Field(default_factory=dict)
