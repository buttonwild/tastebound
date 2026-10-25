"""Operator-only sidebar panel: live safety diagnostics + Tendem restaurant check.

Public visitors only see a passphrase box. Unlocking lasts 30 minutes per
browser session. Creating or approving a Tendem task additionally needs
TASTEBOUND_TENDEM_LIVE=1 on the server.
"""

from __future__ import annotations

import time

import streamlit as st

from .errands import AccessDenied, ErrandError, ErrandsClient, OperatorGate, RestaurantCheck
from .limits import QuotaExceeded
from .style import YouCamClient, YouCamError

UNLOCK_SECONDS = 30 * 60


def _unlocked() -> bool:
    return st.session_state.get("operator_until", 0) > time.time()


def render_operator_panel(gate: OperatorGate, youcam: YouCamClient | None, errands: ErrandsClient | None) -> None:
    with st.sidebar.expander("🔐 Operator"):
        if not gate.enabled:
            st.caption("Operator mode is disabled on this deployment.")
            return
        if not _unlocked():
            attempt = st.text_input("Passphrase", type="password", key="operator_attempt")
            if st.button("Unlock"):
                try:
                    if gate.verify(attempt):
                        st.session_state["operator_until"] = time.time() + UNLOCK_SECONDS
                        st.session_state.pop("operator_attempt", None)
                        st.rerun()
                    st.error("Wrong passphrase.")
                except AccessDenied as exc:
                    st.error(str(exc))
            return

        st.success("Operator mode (30 min)")
        if st.button("Lock"):
            st.session_state.pop("operator_until", None)
            st.rerun()

        st.markdown("**Live safety checks** (same code path as paid calls)")
        if youcam is not None:
            try:
                s = youcam.floor_status()
                st.caption(f"YouCam: balance {s['balance']:.0f} units · floor {s['floor']:.0f} · headroom {s['headroom']:.0f} · {'✅ active' if s['allowed'] else '⛔ blocking'}")
            except YouCamError as exc:
                st.caption(f"YouCam: balance check failed, paid tasks blocked ({exc})")
        if errands is not None:
            try:
                s = errands.floor_status()
                g = errands.guard.snapshot()
                st.caption(
                    f"Tendem: balance ${s['balance']:.2f} · floor ${s['floor']:.2f} · caps ${g['max_task_usd']:.0f}/task, "
                    f"${g['spent_usd']:.2f} of ${g['max_total_usd']:.0f} used, {g['tasks_approved']}/{g['max_tasks']} tasks · "
                    f"{'armed' if errands.live else 'disarmed'}"
                )
            except (ErrandError, QuotaExceeded) as exc:
                st.caption(f"Tendem: balance check failed ({exc})")

        if errands is None:
            return
        st.markdown("**Restaurant check (Tendem)**")
        with st.form("errand"):
            restaurant = st.text_input("Restaurant")
            address = st.text_input("Address")
            city = st.text_input("City")
            phone = st.text_input("Phone (optional)")
            date = st.text_input("Date of call (e.g. Saturday 25 October 2026)")
            preview = st.form_submit_button("Preview brief")
        if preview:
            try:
                check = RestaurantCheck(restaurant, address, city, date, phone or None)
                st.session_state["errand_check"] = check
                st.code(check.brief(), language="text")
            except ValueError as exc:
                st.error(str(exc))
        check = st.session_state.get("errand_check")
        if check and st.button("Create Tendem task", disabled=not errands.live):
            try:
                st.session_state["errand_task"] = errands.submit(check, operator=True)
            except (AccessDenied, ErrandError, QuotaExceeded) as exc:
                st.error(str(exc))
        if not errands.live:
            st.caption("Live sending is disarmed (TASTEBOUND_TENDEM_LIVE is not 1).")
        task_id = st.session_state.get("errand_task")
        if task_id:
            st.caption(f"Task: {task_id}")
            quote = errands.quote(task_id)
            if not quote.quoted:
                st.info(f"Waiting for Tendem's quote (state: {quote.state}). Refresh in a minute.")
                return
            st.markdown(f"**Quote: {quote.price_display}** (cap ${errands.guard.max_task_usd:.0f})")
            confirm = st.checkbox(f"I confirm this restaurant, the brief above and the price {quote.price_display}")
            if st.button(f"Approve {quote.price_display}", disabled=not (confirm and errands.live)):
                try:
                    errands.approve(task_id, quote.price_display, operator=True)
                    st.success("Approved. Tendem will call; results arrive in a few hours.")
                except (AccessDenied, ErrandError, QuotaExceeded) as exc:
                    st.error(str(exc))
