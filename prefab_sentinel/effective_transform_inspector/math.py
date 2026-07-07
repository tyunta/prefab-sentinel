from __future__ import annotations

from math import sqrt


def _add_vector(
    lhs: tuple[float, float, float],
    rhs: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (
        lhs[0] + rhs[0],
        lhs[1] + rhs[1],
        lhs[2] + rhs[2],
    )

def _scale_vector(
    lhs: tuple[float, float, float],
    rhs: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (
        lhs[0] * rhs[0],
        lhs[1] * rhs[1],
        lhs[2] * rhs[2],
    )

def _rotate_vector(
    vector: tuple[float, float, float],
    rotation: tuple[float, float, float, float],
) -> tuple[float, float, float]:
    qx, qy, qz, qw = _normalize_quaternion(rotation)
    vx, vy, vz = vector
    uv = (
        qy * vz - qz * vy,
        qz * vx - qx * vz,
        qx * vy - qy * vx,
    )
    uuv = (
        qy * uv[2] - qz * uv[1],
        qz * uv[0] - qx * uv[2],
        qx * uv[1] - qy * uv[0],
    )
    return (
        vx + 2.0 * (qw * uv[0] + uuv[0]),
        vy + 2.0 * (qw * uv[1] + uuv[1]),
        vz + 2.0 * (qw * uv[2] + uuv[2]),
    )

def _multiply_quaternion(
    lhs: tuple[float, float, float, float],
    rhs: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    ax, ay, az, aw = lhs
    bx, by, bz, bw = rhs
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )

def _normalize_quaternion(
    quat: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    length = sqrt(sum(part * part for part in quat))
    if length == 0:
        return (0.0, 0.0, 0.0, 1.0)
    return (
        quat[0] / length,
        quat[1] / length,
        quat[2] / length,
        quat[3] / length,
    )

def _as_list(values: tuple[float, ...]) -> list[float]:
    return [_clean_float(value) for value in values]

def _clean_float(value: float) -> float:
    rounded = round(float(value), 10)
    if rounded == 0:
        return 0.0
    return rounded
