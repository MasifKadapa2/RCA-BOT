"use client";

import { cn } from "@/lib/utils";
import type { ButtonHTMLAttributes } from "react";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "default" | "outline";
}

export function Button({ variant = "default", className, ...props }: ButtonProps) {
  const styles =
    variant === "outline"
      ? "border border-input bg-white hover:bg-slate-50 text-slate-900"
      : "bg-slate-900 text-white hover:bg-slate-700";
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center rounded-md px-4 py-2 text-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-50",
        styles,
        className
      )}
      {...props}
    />
  );
}
