using RailGo.ViewModels.Pages.StationToStation;

using Microsoft.UI.Xaml.Controls;

namespace RailGo.Views.Pages.StationToStation;

public sealed partial class Ticket_GeneratePage : Page
{
    public Ticket_GenerateViewModel ViewModel
    {
        get;
    }

    public Ticket_GeneratePage()
    {
        ViewModel = App.GetService<Ticket_GenerateViewModel>();
        InitializeComponent();
    }
}
