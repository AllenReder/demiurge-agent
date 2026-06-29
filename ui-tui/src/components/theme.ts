export const colors = {
  background: "#111113",
  border: "#3f3f46",
  code: "#79c0ff",
  codeBg: "#1f242c",
  panel: "#18181b",
  panelStrong: "#222228",
  assistantBlockBg: "#20242e",
  text: "#e8e8ea",
  textStrong: "#ffffff",
  muted: "#9a9aa3",
  placeholder: "#707078",
  user: "#8cb0ff",
  userGutter: "#9cc9ff",
  userBubble: "#20242e",
  assistant: "#7ee787",
  system: "#d2a8ff",
  warning: "#f2cc60",
  error: "#ff7b72",
  success: "#7ee787",
  notice: "#79c0ff",
  link: "#79c0ff",
  selected: "#ffffff",
  slashPanelBg: "#141416",
  slashSelectedBg: "#2a2d36",
}

export type ThemeColors = typeof colors

export function themedColors(input: { demiurge_theme_color?: string; user_theme_color?: string }): ThemeColors {
  const demiurge = input.demiurge_theme_color || colors.assistant
  const user = input.user_theme_color || colors.userGutter
  return {
    ...colors,
    assistant: demiurge,
    notice: demiurge,
    warning: demiurge,
    user,
    userGutter: user,
  }
}
