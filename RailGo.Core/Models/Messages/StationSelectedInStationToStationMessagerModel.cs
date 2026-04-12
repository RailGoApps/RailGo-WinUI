using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using RailGo.Core.Models.QueryDatas;

namespace RailGo.Core.Models.Messages;
public class StationSelectedInStationToStationMessagerModel
{
    public string MessagerName { get; set; }
    public StationPreselectResult Data { get; set; }
}