// Мок компактной таблицы модератора (50 строк в проде, 2 в тестах).
module.exports.loveTableMock = function loveTableMock() {
  return {
    batch_id: 'love_confession_50',
    label: 'Признание в любви и чувства',
    stats: {total:2,direct_links:1,empty_link_fields:1,pending:2,verified:0,rejected:0,
      version_note_required:true,verified_by_human:false},
    items: [
      {batch_rank:1,candidate_id:501,scene_id:901,movie_title:'Амели',movie_year:2001,
       film_title:'Амели (2001)',film_url:'https://rutube.ru/video/abc/',film_provider:'Rutube',
       film_link_kind:'direct',timecode_label:'0:43:04',timecode_end_label:'0:43:58',
       timecode_seconds:2584,timecode_end_seconds:2638,version_note:'',status:'pending',
       status_label:'к проверке',verified:false},
      {batch_rank:2,candidate_id:502,scene_id:902,movie_title:'Асса',movie_year:1987,
       film_title:'Асса (1987)',film_url:'',film_provider:'',film_link_kind:'empty',
       timecode_label:'1:36:55',timecode_end_label:'1:37:30',timecode_seconds:5815,
       timecode_end_seconds:5850,version_note:'',status:'pending',status_label:'к проверке',verified:false},
    ],
  };
};
module.exports.loveBatchMock = module.exports.loveTableMock;
