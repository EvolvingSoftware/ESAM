#!/usr/bin/env python3
"""Stripe Link CLI integration for the Tether AI-powered debt collections system.

This module provides functions to create payment links, check payment status,
and generate test card payment links using the Stripe Link CLI (`link-cli`).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any


def _get_stripe_mode() -> str:
    """Return the current Stripe mode (live or test) from the TETHER_STRIPE_MODE env var."""
    return os.environ.get("TETHER_STRIPE_MODE", "test").lower()


def _run_link_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Execute a link-cli command and return the CompletedProcess result.

    Args:
        args: Additional arguments to pass to the link-cli command.

    Raises:
        FileNotFoundError: If link-cli is not installed.
        subprocess.CalledProcessError: If the command exits with a non-zero status.
    """
    cmd = ["link-cli"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )
        return result
    except FileNotFoundError:
        raise FileNotFoundError(
            "link-cli is not installed. Please install the Stripe Link CLI: "
            "https://docs.stripe.com/link/connecting-your-application"
        )


def create_payment_link(amount_cents: int, description: str) -> str:
    """Create a one-time Stripe Link payment link.

    Args:
        amount_cents: The payment amount in cents (e.g., 1000 for $10.00).
        description: A description for the payment link.

    Returns:
        The generated payment URL.

    Raises:
        ValueError: If amount_cents is not a positive integer.
        RuntimeError: If the link-cli command fails.
    """
    if not isinstance(amount_cents, int) or amount_cents <= 0:
        raise ValueError("amount_cents must be a positive integer")

    mode = _get_stripe_mode()
    args = [
        "spend-request",
        "create",
        "--format", "json",
        "--amount", str(amount_cents),
        "--currency", "usd",
        "--description", description,
        "--mode", mode,
    ]

    try:
        result = _run_link_cli(args)
        data = json.loads(result.stdout)
        return data["url"]
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Failed to create payment link: {exc.stderr.strip()}"
        ) from exc
    except (json.JSONDecodeError, KeyError) as exc:
        raise RuntimeError(
            f"Failed to parse link-cli output: {exc}"
        ) from exc


def check_payment_status(link_id: str) -> dict[str, Any]:
    """Check the payment status of a Stripe Link payment link.

    Args:
        link_id: The unique identifier for the payment link.

    Returns:
        A dictionary containing status information with keys like
        'status', 'paid', 'link_id', etc.

    Raises:
        ValueError: If link_id is empty.
        RuntimeError: If the link-cli command fails.
    """
    if not link_id or not link_id.strip():
        raise ValueError("link_id must not be empty")

    mode = _get_stripe_mode()
    args = [
        "spend-request",
        "get",
        "--format", "json",
        "--id", link_id.strip(),
        "--mode", mode,
    ]

    try:
        result = _run_link_cli(args)
        data = json.loads(result.stdout)
        return {
            "link_id": link_id,
            "status": data.get("status", "unknown"),
            "paid": data.get("paid", False),
            "details": data,
        }
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Failed to check payment status: {exc.stderr.strip()}"
        ) from exc
    except (json.JSONDecodeError, KeyError) as exc:
        raise RuntimeError(
            f"Failed to parse link-cli output: {exc}"
        ) from exc


def generate_test_card_link(amount_cents: int) -> str:
    """Generate a test-mode payment link using the Stripe test card (4242 4242 4242 4242).

    This function forces test mode regardless of the TETHER_STRIPE_MODE setting,
    and includes the test card in the description for demo purposes.

    Args:
        amount_cents: The payment amount in cents.

    Returns:
        The generated test payment URL.

    Raises:
        ValueError: If amount_cents is not a positive integer.
        RuntimeError: If the link-cli command fails.
    """
    if not isinstance(amount_cents, int) or amount_cents <= 0:
        raise ValueError("amount_cents must be a positive integer")

    args = [
        "spend-request",
        "create",
        "--format", "json",
        "--amount", str(amount_cents),
        "--currency", "usd",
        "--description", "Test Payment (Card: 4242 4242 4242 4242)",
        "--mode", "test",
    ]

    try:
        result = _run_link_cli(args)
        data = json.loads(result.stdout)
        return data["url"]
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Failed to create test card payment link: {exc.stderr.strip()}"
        ) from exc
    except (json.JSONDecodeError, KeyError) as exc:
        raise RuntimeError(
            f"Failed to parse link-cli output: {exc}"
        ) from exc


def main() -> None:
    """Demonstrate all three payment link functions."""
    print(f"Current Stripe mode: {_get_stripe_mode()}\n")

    # 1. Create a payment link
    print("=== Creating Payment Link ===")
    try:
        link_url = create_payment_link(5000, "Debt settlement - Invoice #12345")
        print(f"Payment link created: {link_url}\n")
    except Exception as exc:
        print(f"Error: {exc}\n")

    # 2. Check payment status (using a dummy link ID for demonstration)
    print("=== Checking Payment Status ===")
    try:
        status = check_payment_status("lnk_demo123")
        print(f"Payment status: {json.dumps(status, indent=2)}\n")
    except Exception as exc:
        print(f"Error: {exc}\n")

    # 3. Generate a test card link
    print("=== Generating Test Card Link ===")
    try:
        test_url = generate_test_card_link(2500)
        print(f"Test card link created: {test_url}\n")
    except Exception as exc:
        print(f"Error: {exc}\n")


if __name__ == "__main__":
    main()
