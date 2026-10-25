import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { Slot } from "radix-ui"

import { cn } from "@/lib/utils"

const buttonVariants = cva(
  cn(
    "group/button inline-flex cursor-pointer items-center justify-center gap-2 font-semibold whitespace-nowrap select-none",
    "disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50",
    "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
    "[&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4"
  ),
  {
    variants: {
      // Glass (DESIGN.md §7.3): the primary action gets the gradient, shimmer and spring press;
      // everything else is a frosted glass button with a fast, non-spring press.
      variant: {
        default: "btn-primary",
        secondary: "btn-glass",
        destructive: "btn-glass text-red-300",
        outline: "btn-glass",
        ghost: "rounded-lg bg-transparent transition-colors duration-150 hover:bg-white/8",
        link: "bg-transparent text-primary hover:underline",
      },
      size: {
        default: "min-h-11 px-4 py-2 text-base",
        xs: "min-h-7 px-2 py-0.5 text-xs",
        sm: "min-h-9 px-3 py-1 text-sm",
        lg: "min-h-12 px-6 py-2.5 text-base lg:text-lg",
        icon: "size-11 p-2",
        "icon-xs": "size-7 p-1",
        "icon-sm": "size-9 p-1.5",
        "icon-lg": "size-12 p-3",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

function Button({
  className,
  variant = "default",
  size = "default",
  asChild = false,
  ...props
}: React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean
  }) {
  const Comp = asChild ? Slot.Root : "button"

  return (
    <Comp
      data-slot="button"
      data-variant={variant}
      data-size={size}
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  )
}

export { Button, buttonVariants }
