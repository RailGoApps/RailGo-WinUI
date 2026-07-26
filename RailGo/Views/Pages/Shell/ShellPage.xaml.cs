using System.Collections.ObjectModel;
using RailGo.AI.Models;
using RailGo.AI.Services;
using RailGo.Contracts.Services;
using RailGo.Helpers;
using RailGo.ViewModels.Pages.Chat;
using RailGo.ViewModels.Pages.Shell;
using RailGo.Views.Pages.Chat;

using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;

using Windows.Storage;
using Windows.System;

namespace RailGo.Views.Pages.Shell;

public sealed partial class ShellPage : Page
{
    private readonly IBackgroundImageService _backgroundImageService;
    private readonly IPythonProcessManager _processManager;
    private readonly IAIChatClient _chatClient;
    private readonly List<NavigationViewItem> _conversationItems = new();
    private bool _loadingConversations;

    public ShellViewModel ViewModel { get; }

    public ShellPage(
        ShellViewModel viewModel,
        IBackgroundImageService backgroundImageService,
        IPythonProcessManager processManager,
        IAIChatClient chatClient)
    {
        ViewModel = viewModel;
        _backgroundImageService = backgroundImageService;
        _processManager = processManager;
        _chatClient = chatClient;
        InitializeComponent();
        Unloaded += OnUnloaded;

        ViewModel.NavigationService.Frame = NavigationFrame;
        ViewModel.NavigationViewService.Initialize(NavigationViewControl);
        NavigationViewControl.ItemInvoked += OnHostItemInvoked;
        ViewModel.NavigationService.Navigated += OnHostNavigated;
        _processManager.StatusChanged += OnBackendStatusChanged;
    }

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        TitleBarHelper.UpdateTitleBar(RequestedTheme);
        KeyboardAccelerators.Add(BuildKeyboardAccelerator(VirtualKey.Left, VirtualKeyModifiers.Menu));
        KeyboardAccelerators.Add(BuildKeyboardAccelerator(VirtualKey.GoBack));

        _backgroundImageService.BackgroundImageChanged -= OnBackgroundImageChanged;
        _backgroundImageService.BackgroundImageChanged += OnBackgroundImageChanged;
        _ = ApplyBackgroundImageAsync(_backgroundImageService.BackgroundImagePath);

        if (_processManager.IsRunning)
            _ = LoadConversationItemsAsync();
    }

    private void OnHostItemInvoked(NavigationView sender, NavigationViewItemInvokedEventArgs args)
    {
        if (args.InvokedItemContainer is not NavigationViewItem item || item.Tag is not string tag)
            return;

        var navigationKey = typeof(ChatViewModel).FullName!;
        if (tag == "chat:new")
        {
            ViewModel.NavigationService.NavigateTo(navigationKey);
            return;
        }

        if (tag.StartsWith("chat:", StringComparison.OrdinalIgnoreCase) &&
            int.TryParse(tag[5..], out var cid))
        {
            ViewModel.NavigationService.NavigateTo(navigationKey, cid);
        }
    }

    private void OnHostNavigated(object sender, Microsoft.UI.Xaml.Navigation.NavigationEventArgs e)
    {
        if (e.SourcePageType != typeof(ChatPage))
            return;

        if (e.Parameter is int cid)
        {
            NavigationViewControl.SelectedItem = _conversationItems.FirstOrDefault(
                item => string.Equals(item.Tag?.ToString(), $"chat:{cid}", StringComparison.OrdinalIgnoreCase))
                ?? AIChatNavItem;
        }
        else
        {
            NavigationViewControl.SelectedItem = AIChatNavItem;
        }
    }

    private void OnBackendStatusChanged(object? sender, bool running)
    {
        if (!running)
            return;
        DispatcherQueue.TryEnqueue(() => _ = LoadConversationItemsAsync());
    }

    private async Task LoadConversationItemsAsync()
    {
        if (_loadingConversations || !_processManager.IsRunning)
            return;

        _loadingConversations = true;
        try
        {
            var conversations = await _chatClient.GetConversationsAsync();
            DispatcherQueue.TryEnqueue(() => RenderConversationItems(conversations));
        }
        finally
        {
            _loadingConversations = false;
        }
    }

    private void RenderConversationItems(IReadOnlyList<ChatConversation> conversations)
    {
        foreach (var item in _conversationItems)
            NavigationViewControl.MenuItems.Remove(item);
        _conversationItems.Clear();

        var insertIndex = NavigationViewControl.MenuItems.IndexOf(RailGPTNewChatNavItem) + 1;
        foreach (var conversation in conversations.OrderByDescending(c => c.UpdatedAt))
        {
            var item = new NavigationViewItem
            {
                Content = string.IsNullOrWhiteSpace(conversation.Title) ? "新对话" : conversation.Title,
                Tag = $"chat:{conversation.Id}",
                Icon = new FontIcon { Glyph = "\uE8BD" },
            };
            ToolTipService.SetToolTip(item, conversation.Title);
            NavigationViewControl.MenuItems.Insert(insertIndex++, item);
            _conversationItems.Add(item);
        }

        RailGPTHistorySeparator.Visibility = _conversationItems.Count > 0
            ? Visibility.Visible
            : Visibility.Collapsed;
    }

    private static KeyboardAccelerator BuildKeyboardAccelerator(VirtualKey key, VirtualKeyModifiers? modifiers = null)
    {
        var accelerator = new KeyboardAccelerator { Key = key };
        if (modifiers.HasValue) accelerator.Modifiers = modifiers.Value;
        accelerator.Invoked += OnKeyboardAcceleratorInvoked;
        return accelerator;
    }

    private static void OnKeyboardAcceleratorInvoked(KeyboardAccelerator sender, KeyboardAcceleratorInvokedEventArgs args)
    {
        args.Handled = App.GetService<INavigationService>().GoBack();
    }

    private void OnUnloaded(object sender, RoutedEventArgs e)
    {
        _backgroundImageService.BackgroundImageChanged -= OnBackgroundImageChanged;
        _processManager.StatusChanged -= OnBackendStatusChanged;
        ViewModel.NavigationService.Navigated -= OnHostNavigated;
        NavigationViewControl.ItemInvoked -= OnHostItemInvoked;
    }

    private void OnBackgroundImageChanged(object? sender, string? imagePath)
    {
        if (DispatcherQueue.HasThreadAccess)
        {
            _ = ApplyBackgroundImageAsync(imagePath);
            return;
        }
        DispatcherQueue.TryEnqueue(() => _ = ApplyBackgroundImageAsync(imagePath));
    }

    private async Task ApplyBackgroundImageAsync(string? imagePath)
    {
        if (string.IsNullOrWhiteSpace(imagePath) || !File.Exists(imagePath))
        {
            RootGrid.Background = null;
            return;
        }

        try
        {
            var file = await StorageFile.GetFileFromPathAsync(imagePath);
            using var stream = await file.OpenReadAsync();
            var bitmapImage = new BitmapImage();
            await bitmapImage.SetSourceAsync(stream);
            RootGrid.Background = new ImageBrush
            {
                ImageSource = bitmapImage,
                Stretch = Stretch.UniformToFill,
                Opacity = 0.25,
                AlignmentX = AlignmentX.Center,
                AlignmentY = AlignmentY.Center,
            };
        }
        catch
        {
            RootGrid.Background = null;
        }
    }
}
