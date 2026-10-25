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
      theme="dark"
      className="toaster group"
      icons={{
        success: <CircleCheckIcon className="size-4" />,
        info: <InfoIcon className="size-4" />,
        warning: <TriangleAlertIcon className="size-4" />,
        error: <OctagonXIcon className="size-4" />,
        loading: <Loader2Icon className="size-4 animate-spin" />,
      }}
      toastOptions={{
        // Fully unstyled so the glass recipe below owns every surface.
        unstyled: true,
        classNames: {
          toast:
            "glass-strong group/toast relative flex w-(--width) items-center gap-3 rounded-2xl bg-slate-900/70 p-4 font-sans text-slate-50 shadow-[0_8px_32px_rgba(0,0,0,0.45)]",
          content: "flex min-w-0 flex-col gap-0.5",
          title: "text-sm font-semibold",
          description: "text-sm text-muted-foreground",
          icon: "shrink-0",
          actionButton:
            "btn-primary ms-auto h-fit min-w-fit shrink-0 px-2 py-1 text-xs font-semibold",
          cancelButton:
            "btn-glass ms-auto h-fit min-w-fit shrink-0 px-2 py-1 text-xs font-medium",
          closeButton:
            "glass-strong absolute -top-2 -start-2 rounded-full p-0.5 transition-colors duration-150 hover:bg-white/20",
          success: "[&_[data-icon]]:text-emerald-300",
          warning: "[&_[data-icon]]:text-amber-300",
          error: "[&_[data-icon]]:text-red-300",
        },
      }}
      {...props}
    />
  )
}

export { Toaster }
