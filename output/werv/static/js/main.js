let now_playing = document.querySelector(".now-playing");
let track_art = document.querySelector(".track-art");
let track_name = document.querySelector(".track-name");
let track_artist = document.querySelector(".track-artist");

let playpause_btn = document.querySelector(".playpause-track");
let next_btn = document.querySelector(".next-track");
let prev_btn = document.querySelector(".prev-track");

let seek_slider = document.querySelector(".seek_slider");
let volume_slider = document.querySelector(".volume_slider");
let curr_time = document.querySelector(".current-time");
let total_duration = document.querySelector(".total-duration");

let track_index = 0;
let isPlaying = false;
let updateTimer;

// Create new audio element
let curr_track = document.createElement('audio');

var current_title = ''
// Define the tracks that have to be played

var bgchanger = window.setInterval(function(){
  $.ajax({
    method: "post",
    url: "/bg",
    data: {
        text: ''
    },
    success: function(res) {
      document.querySelector('.bg').style.backgroundImage = `linear-gradient(to bottom, rgba(245, 246, 252, 0.1), rgba(117, 19, 93, 0.2)), url('/static/images/tracks/${res.ind}.png')`;
        document.querySelector('.track-art').style.backgroundImage = `url('/static/images/tracks/${res.ind}.png')`;
        track_name.innerHTML = res.title;
        track_artist.innerHTML = res.artist;
    }
  });
}, 1000);

var StatusUpdate = window.setInterval(function(){
  $.ajax({
    method: "post",
    url: "/status",
    data: {
        text: ''
    },
    success: function(res) {
      if (res=='playing' && !isPlaying) playTrack();
      else if (res=='paused' && isPlaying) pauseTrack();
    }
  });
}, 1000);


var deleter = window.setInterval(function(){
  $.ajax({
    method: "post",
    url: "/delete",
    data: {
        text: ''
    },
    success: function(res) {
      console.log('deleted previous catch')
    }
  });
}, 10000);


function playpauseTrack() {
  // if (!isPlaying) playTrack();
  // else pauseTrack();
      $.ajax({
          method: "post",
          url: "/pp",
          data: {
              text: ''
          },
          success: function(res) {
            // location.reload();
            // console.log(res);
            if (!isPlaying) playTrack();
            else pauseTrack();
          }
      });
}

function qrcode() {
      $.ajax({
          method: "post",
          url: "/qr",
          data: {
              text: ''
          },
          success: function(res) {
            console.log('qr_showed')
          }
      });
}

function playTrack() {
  // curr_track.play();
  isPlaying = true;
  playpause_btn.innerHTML = '<i class="fa fa-pause-circle fa-5x"></i>';
}

function pauseTrack() {
  // curr_track.pause();
  isPlaying = false;
  playpause_btn.innerHTML = '<i class="fa fa-play-circle fa-5x"></i>';;
}

function nextTrack() {
  $.ajax({
    method: "post",
    url: "/next",
    data: {
        text: ''
    },
    success: function(res) {
      console.log('nexed')
    }
});
}

function prevTrack() {
  $.ajax({
    method: "post",
    url: "/pre",
    data: {
        text: ''
    },
    success: function(res) {
      console.log('pred')
    }
});
}



function setVolume() {
  curr_track.volume = volume_slider.value / 100;
}




