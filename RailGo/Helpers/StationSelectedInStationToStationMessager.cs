using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.Messaging;
using CommunityToolkit.Mvvm.Messaging.Messages;
using Windows.System;
using WinUIEx.Messaging;

namespace RailGo.Helpers;

public class StationSelectedInStationToStationMessager : ValueChangedMessage<User>
{
    public StationSelectedInStationToStationMessager(User user) : base(user) { }
}
