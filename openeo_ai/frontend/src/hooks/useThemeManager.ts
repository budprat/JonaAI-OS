import { useState, useCallback } from "react";

export interface ThemeManager {
  isDark: boolean;
  handleToggleTheme: () => void;
  handleThemeChange: (theme: "system" | "light" | "dark") => void;
}

/**
 * useThemeManager - Manages dark/light/system theme state.
 *
 * Toggles the `dark` class on `document.documentElement` and
 * tracks the current theme in local component state.
 */
export function useThemeManager(): ThemeManager {
  const [isDark, setIsDark] = useState(false);

  const handleToggleTheme = useCallback(() => {
    setIsDark((prev) => !prev);
    document.documentElement.classList.toggle("dark");
  }, []);

  const handleThemeChange = useCallback(
    (theme: "system" | "light" | "dark") => {
      if (theme === "dark") {
        document.documentElement.classList.add("dark");
        setIsDark(true);
      } else if (theme === "light") {
        document.documentElement.classList.remove("dark");
        setIsDark(false);
      } else {
        const prefersDark = window.matchMedia(
          "(prefers-color-scheme: dark)",
        ).matches;
        if (prefersDark) {
          document.documentElement.classList.add("dark");
          setIsDark(true);
        } else {
          document.documentElement.classList.remove("dark");
          setIsDark(false);
        }
      }
    },
    [],
  );

  return { isDark, handleToggleTheme, handleThemeChange };
}
