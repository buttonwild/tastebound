"""Streamlit panel: "Pack for your destination" — outfit try-on + skin check (YouCam).

Photos stay in session memory only: never written to disk or logged. Result
image URLs are short-lived and shown directly, never stored.
"""

from __future__ import annotations

import streamlit as st

from .concierge import TripContext
from .limits import QuotaExceeded
from .live_info import TavilyClient, TavilyError
from .style import CLOTH_UNITS, YouCamClient, YouCamError, skin_units

MAX_TRYONS_PER_SESSION = 3
MAX_SKIN_PER_SESSION = 1
SKIN_CONCERNS = ["moisture", "oiliness", "redness", "radiance"]  # 4 SD concerns = cheapest tier

FRIENDLY_ERRORS = {
    "error_src_face_too_small": "Your face is too small in the photo. Use a close, front-facing selfie in good light.",
}


def _friendly(exc: Exception) -> str:
    text = str(exc)
    for code, msg in FRIENDLY_ERRORS.items():
        if code in text:
            return msg
    if isinstance(exc, QuotaExceeded):
        return "The demo's try-on limit has been reached. Outfit ideas still work; try-on returns later."
    return "The try-on service couldn't finish this one. Please try another photo."


def _skin_tips(scores: dict[str, float]) -> list[str]:
    """Cosmetic, non-medical tips from YouCam ui_scores (higher = better)."""
    tips = []
    if scores.get("moisture", 100) < 60:
        tips.append("Hydration is your priority: pack a richer moisturiser, since travel and heating dry skin out.")
    if scores.get("oiliness", 100) < 60:
        tips.append("Shine-prone: a light gel moisturiser and blotting papers beat heavy creams.")
    if scores.get("redness", 100) < 60:
        tips.append("Some redness: pack a fragrance-free, soothing product and avoid new actives while away.")
    if scores.get("radiance", 100) < 60:
        tips.append("For a fresher look on long travel days: SPF, water, and a gentle vitamin C serum.")
    return tips or ["Your skin looks balanced. Keep your usual routine and pack SPF."]


@st.cache_data(ttl=3600, show_spinner=False)
def _garments(_client: YouCamClient):
    return _client.list_garments(page_size=20)  # free call


def render_style_panel(client: YouCamClient | None, ctx: TripContext, tavily: TavilyClient | None) -> None:
    where = ctx.destination.city if ctx.destination and ctx.destination.city else "your destination"
    with st.expander(f"👗 Pack for {where}: try outfits on & check your skin (YouCam)"):
        if client is None:
            st.caption("Add `YOUCAM_API_KEY` to enable virtual try-on.")
            return
        state = st.session_state.setdefault("style", {"tryons": 0, "skin": 0})
        st.caption(
            "Upload a photo to see yourself in an outfit (YouCam Apparel Virtual Try-On) and get skin tips for the trip "
            "(YouCam Skin AI). Your photo is sent to YouCam only for this request and isn't stored by Tastebound."
        )
        consent = st.checkbox("I agree to send my photo to YouCam for processing")
        photo = st.file_uploader("Full-body photo for try-on, or a close selfie for the skin check", type=["jpg", "jpeg", "png"])
        if photo is None or not consent:
            return

        data = photo.getvalue()
        file_key = f"file_id:{photo.file_id}"
        if file_key not in st.session_state:
            try:
                with st.spinner("Uploading..."):
                    st.session_state[file_key] = client.upload(data, photo.type or "image/jpeg", photo.name)
            except YouCamError as exc:
                st.error(_friendly(exc))
                return
        file_id = st.session_state[file_key]

        col_tryon, col_skin = st.columns(2)
        with col_tryon:
            try:
                garments = _garments(client)
            except YouCamError:
                garments = []
            if garments:
                choice = st.selectbox("Outfit", garments, format_func=lambda g: f"{g.label} ({g.category})")
                if choice.thumb:
                    st.image(choice.thumb, width=120)
                left = MAX_TRYONS_PER_SESSION - state["tryons"]
                if st.button(f"Try it on ({CLOTH_UNITS} units) · {left} left", disabled=left <= 0):
                    try:
                        with st.spinner("Dressing you up... (about 20–60 s)"):
                            result = client.try_on(choice, src_file_id=file_id)
                        state["tryons"] += 1
                        st.image(result.image_url, caption=f"You in {choice.label}")
                    except (YouCamError, QuotaExceeded) as exc:
                        st.warning(_friendly(exc))
        with col_skin:
            left = MAX_SKIN_PER_SESSION - state["skin"]
            if st.button(f"Skin check for the trip ({skin_units(len(SKIN_CONCERNS))} units)", disabled=left <= 0):
                try:
                    with st.spinner("Analysing skin..."):
                        report = client.analyze_skin(SKIN_CONCERNS, src_file_id=file_id)
                    state["skin"] += 1
                    for name, score in report.scores.items():
                        st.progress(min(max(score, 0), 100) / 100, text=f"{name.capitalize()}: {score:.0f}/100")
                    for tip in _skin_tips(report.scores):
                        st.markdown(f"- {tip}")
                    if tavily is not None and ctx.destination:
                        try:
                            weather = tavily.search(f"weather forecast {where} this week", max_results=2, topic="general", time_range="week")
                            if weather.get("answer"):
                                st.markdown(f"**Weather in {where}:** {weather['answer']}")
                            for r in weather.get("results", [])[:2]:
                                st.caption(f"[{r['title']}]({r['url']})")
                        except TavilyError:
                            pass
                    st.caption("Cosmetic suggestions only, not medical advice.")
                except (YouCamError, QuotaExceeded) as exc:
                    st.warning(_friendly(exc))
