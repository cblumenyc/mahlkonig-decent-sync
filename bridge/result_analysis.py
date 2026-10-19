import math


# Bridge reporting tolerance, not a claimed firmware deadband or sensor accuracy.
# Native distances use tenths of a micrometre. This is the existing arrival
# tolerance used when confirming movement; do not widen it for small changes.
POSITION_TOLERANCE_DUM = 10
MOVEMENT_CONFIRMATION_GRACE_S = 45


def movement_warning_due(record, now):
    """Receipt can precede motor readback; warn only on a fault or persistent uncertainty."""
    if record.get('outcome') != 'movement_pending':
        return False
    if record.get('assessment', {}).get('movement_fault_observed'):
        return True
    since = record.get('movement_pending_since')
    # Historical pending records without a persisted start retain their warning.
    return not finite_number(since) or now - since >= MOVEMENT_CONFIRMATION_GRACE_S


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def small_change_within_tolerance(before_target, after_target, driver_before, driver_after, native):
    """A tiny new target already fits the measured position; movement is unproven.

    Require both readings near the new target, normal idle hardware, no native
    pending brew and no active motion alert. Missing evidence cannot clear a warning.
    """
    if not all(finite_number(v) for v in (before_target, after_target)):
        return False
    if not 0 < abs(after_target - before_target) <= POSITION_TOLERANCE_DUM:
        return False
    before, after = driver_before.get('data', {}), driver_after.get('data', {})
    for sensor in (before, after):
        if sensor.get('ddSensorFailed') is not False or not all(
                finite_number(sensor.get(k)) for k in ('ddValueDum', 'agsaHallSensorCounts')):
            return False
        if abs(sensor['ddValueDum'] - after_target) > POSITION_TOLERANCE_DUM:
            return False
    if any(after.get(k) != value for k, value in (
            ('agsaStatus', 'Normal'), ('grindState', 'Idle'), ('gbsState', 'Idle'))):
        return False
    if 'pending_brew' not in native or native['pending_brew'] is not None:
        return False
    alerts = native.get('motion_alert_events')
    return isinstance(alerts, list) and all(a.get('alert_active') == 0 for a in alerts)


def large_change_hold(plan, after):
    """Recognize V01.16-24 AlgorithmV3's >50 µm early-return state change.

    This inference requires the exact before/after bookkeeping transition;
    a small brew-time error alone is not evidence of this hold.
    """
    before = plan["baseline"]
    pending = before.get("pending_brew") or {}
    current_target = pending.get("targetGrindSizeDum")
    if not isinstance(current_target, int):
        return False
    for old in before["algorithm_states"]:
        identity = tuple(old[k] for k in ("recipe_index", "brew_device", "group_head"))
        matches = [new for new in after["algorithm_states"]
                   if tuple(new[k] for k in ("recipe_index", "brew_device", "group_head")) == identity]
        if len(matches) != 1 or old["recipe_index"] != pending.get("recipeIndex"):
            continue
        previous, updated = old["data"], matches[0]["data"]
        prior_target = previous.get("lastTargetDdInDeciMicroMeters")
        if (previous.get("type") == "GbS3" and isinstance(prior_target, int)
                and abs(current_target - prior_target) > 500
                and updated.get("lastTargetDdInDeciMicroMeters") == current_target
                and previous.get("lastAdjustmentInDeciMicroMeters") is not None
                and updated.get("lastAdjustmentInDeciMicroMeters") == previous["lastAdjustmentInDeciMicroMeters"]
                and updated.get("filteredDdTimesBrew") == previous.get("filteredDdTimesBrew")):
            return True
    return False


def assess_xenia(plan, after, driver_before, driver_after):
    before = plan["baseline"]
    prior_ids = {b["brew_id"] for b in before["brews"]}
    shot = plan["shot"]
    rows = [b for b in after["brews"] if b["brew_id"] not in prior_ids
            and b["grind_id"] == plan["grind_id"]]
    matching = [b for b in rows if b["brew_duration"] == shot["duration_ms"]
                and b["brew_mass"] == shot["weight_mg"] and b["brew_validity"] == 1]
    sensor_before, sensor_after = driver_before.get("data", {}), driver_after.get("data", {})
    fields = ("ddValueDum", "agsaHallSensorCounts", "agsaStatus", "ddSensorFailed", "grindState", "gbsState", "hmiTime")
    distance_before, distance_after = sensor_before.get("ddValueDum"), sensor_after.get("ddValueDum")
    hall_before, hall_after = sensor_before.get("agsaHallSensorCounts"), sensor_after.get("agsaHallSensorCounts")
    physical = None
    if (all(finite_number(v) for v in (distance_before, distance_after, hall_before, hall_after))
            and sensor_before.get("ddSensorFailed") is False and sensor_after.get("ddSensorFailed") is False):
        physical = distance_before != distance_after and hall_before != hall_after
    return {
        "shot_id": shot["shot_id"], "grind_id": plan["grind_id"],
        "new_linked_brews": rows, "exact_result_stored": len(matching) == 1,
        "pending_cleared": bool(before["pending_brew"]) and not after["pending_brew"],
        "algorithm_state_changed": before["algorithm_states"] != after["algorithm_states"],
        "recipe_state_changed": before["recipes"] != after["recipes"],
        "prediction_changed": before["grind_size_prediction"] != after["grind_size_prediction"],
        "hold_after_large_adjustment": len(matching) == 1 and large_change_hold(plan, after),
        "physical_movement_observed": physical,
        "movement_fault_observed": sensor_after.get('ddSensorFailed') is True or any(
            alert.get('alert_active') == 1 for alert in after.get('motion_alert_events', [])),
        "driver_before": {k: sensor_before[k] for k in fields if k in sensor_before},
        "driver_after": {k: sensor_after[k] for k in fields if k in sensor_after},
        "note": "Physical movement is inferred from two sensor readbacks; null means unavailable.",
    }
