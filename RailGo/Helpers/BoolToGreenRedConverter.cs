using Microsoft.UI;
using Microsoft.UI.Xaml.Data;
using Microsoft.UI.Xaml.Media;
using WColor = Windows.UI.Color;

namespace RailGo.Helpers;

/// <summary>
/// Converts bool to a green SolidColorBrush when true, red/gray when false.
/// Used for backend status indicator dots.
/// </summary>
public class BoolToGreenRedConverter : IValueConverter
{
    private static readonly SolidColorBrush GreenBrush = new(WColor.FromArgb(255, 34, 197, 94));
    private static readonly SolidColorBrush RedBrush = new(WColor.FromArgb(255, 239, 68, 68));
    private static readonly SolidColorBrush GrayBrush = new(WColor.FromArgb(255, 156, 163, 175));

    public object Convert(object value, Type targetType, object parameter, string language)
    {
        if (value is bool b)
            return b ? GreenBrush : RedBrush;
        return GrayBrush;
    }

    public object ConvertBack(object value, Type targetType, object parameter, string language)
    {
        throw new NotImplementedException();
    }
}
