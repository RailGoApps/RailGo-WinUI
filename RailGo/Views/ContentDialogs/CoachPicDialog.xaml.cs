using System.Diagnostics;
using System.IO;
using System.Threading;
using Microsoft.UI.Text;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using RailGo.Core.Models.QueryDatas;
using RailGo.Services;
using Windows.UI;

namespace RailGo.Views.ContentDialogs;

/// <summary>
/// 列车车厢图弹窗：调用 getCoachPic 接口，一行展示三节车厢，可上下滚动查看整列车。
/// </summary>
public sealed partial class CoachPicDialog : ContentDialog
{
    private readonly string _trainNumber;
    private readonly QueryService _queryService = App.GetService<QueryService>();
    private bool _isLoaded;

    public CoachPicDialog(string trainNumber)
    {
        InitializeComponent();
        _trainNumber = trainNumber;
        TrainTitleText.Text = trainNumber;
        TrainSubtitleText.Text = "正在获取车厢信息…";

        this.Opened += OnDialogOpened;
    }

    private async void OnDialogOpened(ContentDialog sender, ContentDialogOpenedEventArgs args)
    {
        if (_isLoaded)
        {
            return;
        }
        _isLoaded = true;
        await LoadCoachPicAsync();
    }

    private async Task LoadCoachPicAsync()
    {
        LoadingRing.IsActive = true;

        try
        {
            var info = await _queryService.QueryCoachPicAsync(_trainNumber);

            if (info == null)
            {
                ShowError("接口暂不可用或当前处于离线模式，未能获取到该车次的车厢图。");
                return;
            }

            var cars = info.CoachPicList?
                .Where(item => !string.IsNullOrWhiteSpace(item.PictureUrl))
                .OrderBy(item => item.PicOrder)
                .ToList();

            if (cars == null || cars.Count == 0)
            {
                ShowError($"未获取到 {_trainNumber} 的车厢图，该车次可能不是动车组。");
                return;
            }

            var subtitleParts = new[] { info.CarType, info.TrainStyle, info.CarCode }
                .Where(part => !string.IsNullOrWhiteSpace(part))
                .Distinct();
            TrainSubtitleText.Text = string.Join(" · ", subtitleParts);

            BuildInfoCards(info);

            CarsPanel.Children.Add(BuildCarRows(cars));

            await DownloadCarImagesAsync(cars);
        }
        catch (Exception ex)
        {
            Trace.WriteLine(ex);
            ShowError($"获取车厢图失败：{ex.Message}");
        }
        finally
        {
            LoadingRing.IsActive = false;
        }
    }

    private void ShowError(string message)
    {
        TrainSubtitleText.Text = string.Empty;
        ErrorText.Text = message;
        ErrorText.Visibility = Visibility.Visible;
        CarsScrollViewer.Visibility = Visibility.Collapsed;
    }

    private void BuildInfoCards(CoachPicInfo info)
    {
        var items = new (string Label, string Value)[]
        {
            ("列车编组", GetCarInfoValue(info, "列车编组")),
            ("最高时速", GetCarInfoValue(info, "最高时速")),
            ("餐车", GetCarInfoValue(info, "餐车")),
            ("列车定员", GetCarInfoValue(info, "列车定员")),
            ("列车长度", GetCarInfoValue(info, "列车长度")),
            ("无障碍车厢", GetCarInfoValue(info, "无障碍车厢")),
        };

        foreach (var (label, value) in items)
        {
            InfoPanel.Children.Add(CreateInfoCard(label, string.IsNullOrWhiteSpace(value) ? " -- " : value));
        }
    }

    private static string GetCarInfoValue(CoachPicInfo info, string name)
    {
        return info.CarInfo?
            .FirstOrDefault(item => string.Equals(item.PictureName, name, StringComparison.OrdinalIgnoreCase))
            ?.PictureValue;
    }

    private static Border CreateInfoCard(string label, string value)
    {
        var panel = new StackPanel { Spacing = 2 };
        panel.Children.Add(new TextBlock
        {
            Text = label,
            FontSize = 12,
            Foreground = GetBrush("TextFillColorSecondaryBrush", Color.FromArgb(255, 128, 128, 128))
        });
        panel.Children.Add(new TextBlock
        {
            Text = value,
            FontSize = 16,
            FontWeight = FontWeights.SemiBold
        });

        return new Border
        {
            Child = panel,
            Padding = new Thickness(12, 6, 12, 6),
            CornerRadius = new CornerRadius(6),
            BorderThickness = new Thickness(1),
            BorderBrush = GetBrush("CardStrokeColorDefaultBrush", Color.FromArgb(60, 128, 128, 128)),
            Background = GetBrush("LayerFillColorDefaultBrush", Color.FromArgb(0, 0, 0, 0)),
            VerticalAlignment = VerticalAlignment.Center
        };
    }

    private static Brush GetBrush(string key, Color fallback)
    {
        if (Application.Current.Resources.TryGetValue(key, out var value) && value is Brush brush)
        {
            return brush;
        }
        return new SolidColorBrush(fallback);
    }

    private FrameworkElement BuildCarRows(List<CoachPicItem> cars)
    {
        var grid = new Grid
        {
            ColumnSpacing = 12,
            RowSpacing = 12
        };

        for (var column = 0; column < 3; column++)
        {
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        }

        for (var row = 0; row < (cars.Count + 2) / 3; row++)
        {
            grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        }

        for (var index = 0; index < cars.Count; index++)
        {
            var card = CreateCarCard(cars[index]);
            Grid.SetRow(card.Root, index / 3);
            Grid.SetColumn(card.Root, index % 3);
            grid.Children.Add(card.Root);
            _carCards.Add((cars[index], card));
        }

        return grid;
    }

    private readonly List<(CoachPicItem Car, CarCard Card)> _carCards = new();

    private CarCard CreateCarCard(CoachPicItem car)
    {
        var image = new Image
        {
            Stretch = Stretch.Uniform,
            HorizontalAlignment = HorizontalAlignment.Center,
            VerticalAlignment = VerticalAlignment.Center
        };

        var ring = new ProgressRing
        {
            IsActive = true,
            Width = 24,
            Height = 24,
            HorizontalAlignment = HorizontalAlignment.Center,
            VerticalAlignment = VerticalAlignment.Center
        };

        var imageHost = new Grid { Height = 150 };
        imageHost.Children.Add(image);
        imageHost.Children.Add(ring);

        var name = new TextBlock
        {
            Text = car.PictureName,
            FontSize = 13,
            TextAlignment = TextAlignment.Center,
            HorizontalAlignment = HorizontalAlignment.Center,
            TextWrapping = TextWrapping.Wrap
        };

        var panel = new StackPanel { Spacing = 6 };
        panel.Children.Add(imageHost);
        panel.Children.Add(name);

        var root = new Border
        {
            Child = panel,
            Padding = new Thickness(8),
            CornerRadius = new CornerRadius(8),
            BorderThickness = new Thickness(1),
            BorderBrush = GetBrush("CardStrokeColorDefaultBrush", Color.FromArgb(60, 128, 128, 128)),
            Background = GetBrush("LayerFillColorDefaultBrush", Color.FromArgb(0, 0, 0, 0)),
            HorizontalAlignment = HorizontalAlignment.Stretch,
            VerticalAlignment = VerticalAlignment.Stretch
        };

        return new CarCard(root, image, ring, name);
    }

    private async Task DownloadCarImagesAsync(List<CoachPicItem> cars)
    {
        using var semaphore = new SemaphoreSlim(5);

        var tasks = _carCards.Select(async pair =>
        {
            await semaphore.WaitAsync();
            try
            {
                var bytes = await _queryService.QueryDownloadImageAsync(pair.Car.PictureUrl);
                if (bytes == null || bytes.Length == 0)
                {
                    pair.Card.Name.Text = $"{pair.Car.PictureName}（图片加载失败）";
                    return;
                }

                using var stream = new MemoryStream(bytes);
                var bitmap = new BitmapImage { DecodePixelWidth = 560 };
                await bitmap.SetSourceAsync(stream.AsRandomAccessStream());
                pair.Card.Image.Source = bitmap;
            }
            catch (Exception ex)
            {
                Trace.WriteLine(ex);
                pair.Card.Name.Text = $"{pair.Car.PictureName}（图片加载失败）";
            }
            finally
            {
                pair.Card.Ring.IsActive = false;
                pair.Card.Ring.Visibility = Visibility.Collapsed;
                semaphore.Release();
            }
        });

        await Task.WhenAll(tasks);
    }

    private sealed record CarCard(Border Root, Image Image, ProgressRing Ring, TextBlock Name);
}
