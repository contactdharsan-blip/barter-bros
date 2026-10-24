import {
  CircleCheckIcon,
  InfoIcon,
  Loader2Icon,
  OctagonXIcon,
  TriangleAlertIcon,
} from "lucide-react"
import { Toaster as Sonner, type ToasterProps } from "sonner"

const Toaster = ({ ...props }: ToasterProps) => {
  return (
    <Sonner
      theme="light"
      className="toaster group"
      icons={{
        success: <CircleCheckIcon className="size-4" />,
        info: <InfoIcon className="size-4" />,
        warning: <TriangleAlertIcon className="size-4" />,
        error: <OctagonXIcon className="size-4" />,
        loading: <Loader2Icon className="size-4 animate-spin" />,
      }}
      toastOptions={{
        // Fully unstyled so the neobrutalist recipe below owns every surface:
        // hard offset shadow, 2px border, square corners, press-down buttons.
        unstyled: true,
        classNames: {
          toast:
            "group/toast relative flex w-(--width) items-center gap-3 rounded border-2 border-border bg-popover p-4 font-sans text-popover-foreground shadow-md",
          content: "flex min-w-0 flex-col gap-0.5",
          title: "font-head text-sm font-medium",
          description: "text-sm text-muted-foreground",
          icon: "shrink-0",
          actionButton:
            "ms-auto h-fit min-w-fit shrink-0 rounded border-2 border-border bg-primary px-2 py-1 text-xs font-medium text-primary-foreground shadow-sm transition-all duration-200 hover:translate-x-0.5 hover:translate-y-0.5 hover:shadow-none",
          cancelButton:
            "ms-auto h-fit min-w-fit shrink-0 rounded border-2 border-border bg-muted px-2 py-1 text-xs font-medium text-foreground shadow-sm transition-all duration-200 hover:translate-x-0.5 hover:translate-y-0.5 hover:shadow-none",
          closeButton:
            "absolute -top-2 -start-2 rounded-full border-2 border-border bg-background p-0.5 transition-colors hover:bg-muted",
          success: "[&_[data-icon]]:text-chart-2",
          warning: "[&_[data-icon]]:text-chart-1",
          error: "[&_[data-icon]]:text-destructive",
        },
      }}
      {...props}
    />
  )
}

export { Toaster }
