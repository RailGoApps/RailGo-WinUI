using System.Runtime.InteropServices;

using Microsoft.UI;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Media;

using Windows.UI;
using Windows.UI.ViewManagement;

namespace RailGo.Helpers;

// Helper class to workaround custom title bar bugs.
// DISCLAIMER: The resource key names and color values used below are subject to change. Do not depend on them.
// https://github.com/microsoft/TemplateStudio/issues/4516
internal class TitleBarHelper
{
    private const int WAINACTIVE = 0x00;
    private const int WAACTIVE = 0x01;
    private const int WMACTIVATE = 0x0006;

    [DllImport("user32.dll")]
    private static extern IntPtr GetActiveWindow();

    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    private static extern IntPtr SendMessage(IntPtr hWnd, int msg, int wParam, IntPtr lParam);

    public static void UpdateTitleBar(ElementTheme theme)
    {
        // MainWindow 构造期间 App.MainWindow 尚未赋值，需判空
        var window = App.MainWindow;
        if (window == null || !window.ExtendsContentIntoTitleBar)
        {
            return;
        }

        if (!AppWindowTitleBar.IsCustomizationSupported())
        {
            return;
        }

        if (theme == ElementTheme.Default)
        {
            // 以窗口内容实际生效的主题为准，避免跟随系统时误判为浅色
            theme = (window.Content as FrameworkElement)?.ActualTheme ?? ElementTheme.Default;
        }

        if (theme == ElementTheme.Default)
        {
            theme = Application.Current.RequestedTheme == ApplicationTheme.Light ? ElementTheme.Light : ElementTheme.Dark;
        }

        var titleBar = window.AppWindow.TitleBar;

        titleBar.ButtonForegroundColor = theme switch
        {
            ElementTheme.Dark => Colors.White,
            ElementTheme.Light => Colors.Black,
            _ => Colors.Transparent
        };

        titleBar.ButtonInactiveForegroundColor = theme switch
        {
            ElementTheme.Dark => Color.FromArgb(0xFF, 0x99, 0x99, 0x99),
            ElementTheme.Light => Color.FromArgb(0xFF, 0x66, 0x66, 0x66),
            _ => Colors.Transparent
        };

        titleBar.ButtonHoverForegroundColor = theme switch
        {
            ElementTheme.Dark => Colors.White,
            ElementTheme.Light => Colors.Black,
            _ => Colors.Transparent
        };

        titleBar.ButtonPressedForegroundColor = theme switch
        {
            ElementTheme.Dark => Colors.White,
            ElementTheme.Light => Colors.Black,
            _ => Colors.Transparent
        };

        // 按钮底色保持透明，让标题栏沿用 Mica / 深色背景
        titleBar.ButtonBackgroundColor = Colors.Transparent;
        titleBar.ButtonInactiveBackgroundColor = Colors.Transparent;

        titleBar.ButtonHoverBackgroundColor = theme switch
        {
            ElementTheme.Dark => Color.FromArgb(0x33, 0xFF, 0xFF, 0xFF),
            ElementTheme.Light => Color.FromArgb(0x33, 0x00, 0x00, 0x00),
            _ => Colors.Transparent
        };

        titleBar.ButtonPressedBackgroundColor = theme switch
        {
            ElementTheme.Dark => Color.FromArgb(0x66, 0xFF, 0xFF, 0xFF),
            ElementTheme.Light => Color.FromArgb(0x66, 0x00, 0x00, 0x00),
            _ => Colors.Transparent
        };

        titleBar.BackgroundColor = Colors.Transparent;

        var hwnd = WinRT.Interop.WindowNative.GetWindowHandle(window);
        if (hwnd == GetActiveWindow())
        {
            SendMessage(hwnd, WMACTIVATE, WAINACTIVE, IntPtr.Zero);
            SendMessage(hwnd, WMACTIVATE, WAACTIVE, IntPtr.Zero);
        }
        else
        {
            SendMessage(hwnd, WMACTIVATE, WAACTIVE, IntPtr.Zero);
            SendMessage(hwnd, WMACTIVATE, WAINACTIVE, IntPtr.Zero);
        }
    }

    public static void ApplySystemThemeToCaptionButtons()
    {
        var frame = App.AppTitlebar as FrameworkElement;
        if (frame != null)
        {
            UpdateTitleBar(frame.ActualTheme);
        }
    }
}
